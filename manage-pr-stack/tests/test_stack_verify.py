"""stack-verify: cache by tree, setup trigger, failure reporting, worktree safety.

Run from the skill directory: python3 -m unittest tests/test_stack_verify.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "scripts" / "stack-verify"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, env=env or GIT_ENV, text=True, capture_output=True, check=False)


def git(cwd: Path, *args: str) -> str:
    proc = run("git", *args, cwd=cwd)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def commit_file(cwd: Path, name: str, content: str, message: str) -> str:
    (cwd / name).write_text(content)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


class VerifyTest(unittest.TestCase):
    """Stack a, b, c on main. deps.lock changes in c only; deps/ is ignored and made by the setup command."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "work"
        git(self.root, "init", "-q", "-b", "main", str(self.repo))
        (self.repo / ".gitignore").write_text("deps/\n")
        git(self.repo, "add", ".gitignore")
        commit_file(self.repo, "deps.lock", "v1\n", "base")
        git(self.repo, "switch", "-q", "-c", "a")
        commit_file(self.repo, "a.txt", "a\n", "a")
        git(self.repo, "switch", "-q", "-c", "b")
        commit_file(self.repo, "b.txt", "b\n", "b")
        git(self.repo, "switch", "-q", "-c", "c")
        commit_file(self.repo, "deps.lock", "v2\n", "c")
        git(self.repo, "switch", "-q", "main")

        self.runs = self.root / "runs.log"
        self.setups = self.root / "setups.log"
        self.env = {**GIT_ENV, "XDG_CACHE_HOME": str(self.root / "cache")}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def verify(self, *extra: str, branches: str = "a,b,c") -> subprocess.CompletedProcess[str]:
        return run(
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(self.repo),
            "--branches",
            branches,
            "--setup",
            f"mkdir -p deps && cp deps.lock deps/installed && echo setup >> {self.setups}",
            "--setup-inputs",
            "deps.lock",
            "--cmd",
            f"echo $(git rev-parse --short HEAD) >> {self.runs}",
            "--cmd",
            "cmp deps.lock deps/installed",
            *extra,
            cwd=self.repo,
            env=self.env,
        )

    def rows(self, proc: subprocess.CompletedProcess[str]) -> dict[str, list[str]]:
        table = proc.stdout.split("\n\n")[0].splitlines()[2:]
        return {line.split()[0]: line.split()[1:] for line in table}

    def run_count(self) -> int:
        return len(self.runs.read_text().splitlines()) if self.runs.exists() else 0

    def test_unchanged_trees_hit_the_cache(self) -> None:
        first = self.verify()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual([row[1] for row in self.rows(first).values()], ["ran", "ran", "ran"])
        self.assertIn("[2] cmp deps.lock deps/installed", first.stdout)

        second = self.verify()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual([row[1] for row in self.rows(second).values()], ["cached", "cached", "cached"])
        self.assertEqual(self.run_count(), 3)

    def test_changed_tree_runs_again(self) -> None:
        self.verify()
        git(self.repo, "switch", "-q", "b")
        commit_file(self.repo, "b.txt", "b2\n", "b2")
        git(self.repo, "switch", "-q", "c")
        git(self.repo, "rebase", "-q", "b")
        git(self.repo, "switch", "-q", "main")

        proc = self.verify()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual({branch: row[1] for branch, row in self.rows(proc).items()}, {"a": "cached", "b": "ran", "c": "ran"})
        self.assertEqual(self.run_count(), 5)

    def test_setup_runs_only_when_its_inputs_change(self) -> None:
        proc = self.verify()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual({branch: row[2] for branch, row in self.rows(proc).items()}, {"a": "ran", "b": "skip", "c": "ran"})
        self.assertEqual(len(self.setups.read_text().splitlines()), 2)

        again = self.verify("--no-cache", branches="c")
        self.assertEqual(self.rows(again)["c"][2], "skip", "deps/ survives git clean -fd, so setup is skipped")

    def test_failures_are_reported_and_not_cached(self) -> None:
        def verify_without_b_txt() -> subprocess.CompletedProcess[str]:
            return run(
                sys.executable,
                str(SCRIPT),
                "--repo",
                str(self.repo),
                "--branches",
                "a,b,c",
                "--cmd",
                f"echo run >> {self.runs}",
                "--cmd",
                "test ! -e b.txt",
                cwd=self.repo,
                env=self.env,
            )

        proc = verify_without_b_txt()
        self.assertEqual(proc.returncode, 1)
        rows = self.rows(proc)
        self.assertEqual(rows["a"][2:4], ["0", "0"])
        self.assertEqual(rows["b"][2:4], ["0", "1"])
        self.assertEqual(rows["c"][2:4], ["0", "1"])
        self.assertIn("2 of 3 head(s) failed", proc.stdout)
        log = Path(proc.stdout.split("  b: ", 1)[1].splitlines()[0])
        self.assertIn("$ test ! -e b.txt", log.read_text())
        self.assertIn("# exit 1", log.read_text())

        again = verify_without_b_txt()
        self.assertEqual({branch: row[1] for branch, row in self.rows(again).items()}, {"a": "cached", "b": "ran", "c": "ran"})

    def test_refuses_a_directory_it_did_not_create(self) -> None:
        other = self.root / "precious"
        other.mkdir()
        (other / "notes.txt").write_text("keep me\n")
        proc = self.verify("--worktree", str(other))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("is not a stack-verify worktree", proc.stderr)
        self.assertEqual((other / "notes.txt").read_text(), "keep me\n")

    def test_remove_worktree(self) -> None:
        self.verify()
        worktree = self.root / "work.stack-verify"
        self.assertTrue(worktree.exists())
        proc = run(sys.executable, str(SCRIPT), "--repo", str(self.repo), "--remove-worktree", cwd=self.repo, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(worktree.exists())


if __name__ == "__main__":
    unittest.main()
