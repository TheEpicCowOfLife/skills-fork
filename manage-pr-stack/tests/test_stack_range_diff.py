"""stack-range-diff: per-PR classification between old and new heads.

Run from the skill directory: python3 -m unittest tests/test_stack_range_diff.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "scripts" / "stack-range-diff"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, env=GIT_ENV, text=True, capture_output=True, check=False)


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


def lines(*values: str) -> str:
    return "".join(f"{value}\n" for value in values)


class RangeDiffTest(unittest.TestCase):
    """Old stack a, b, c, d, e on main. New stack: a untouched, b's commit edited, c gains a commit, d and e swap."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        origin = root / "origin.git"
        self.repo = root / "work"
        git(root, "init", "-q", "--bare", "-b", "main", str(origin))
        git(root, "clone", "-q", str(origin), str(self.repo))

        commit_file(self.repo, "base.txt", "base\n", "base")
        git(self.repo, "push", "-q", "origin", "HEAD:main")
        git(self.repo, "switch", "-q", "-c", "a")
        commit_file(self.repo, "a.txt", lines("a1", "a2", "a3", "a4", "a5", "a6"), "a: add a")
        git(self.repo, "switch", "-q", "-c", "b")
        commit_file(self.repo, "b.txt", lines("b1", "b2", "b3", "b4", "b5", "b6"), "b: add b")
        git(self.repo, "switch", "-q", "-c", "c")
        commit_file(self.repo, "c.txt", lines("c1", "c2"), "c: add c")
        git(self.repo, "switch", "-q", "-c", "d")
        commit_file(self.repo, "d.txt", lines("d1"), "d: add d")
        git(self.repo, "switch", "-q", "-c", "e")
        commit_file(self.repo, "e.txt", lines("e1"), "e: add e")
        git(self.repo, "push", "-q", "origin", "a", "b", "c", "d", "e")

        git(self.repo, "switch", "-q", "b")
        (self.repo / "b.txt").write_text(lines("b1", "b2", "b3", "B4", "b5", "b6"))
        git(self.repo, "commit", "-q", "-a", "--amend", "--no-edit")
        git(self.repo, "switch", "-q", "c")
        git(self.repo, "rebase", "-q", "--onto", "b", "origin/b", "c")
        commit_file(self.repo, "c.txt", lines("c1", "c2", "c3"), "c: add c3")
        git(self.repo, "switch", "-q", "e")
        git(self.repo, "rebase", "-q", "--onto", "c", "origin/d", "e")
        git(self.repo, "switch", "-q", "d")
        git(self.repo, "rebase", "-q", "--onto", "e", "origin/c", "d")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def range_diff(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return run(sys.executable, str(SCRIPT), "--repo", str(self.repo), "--base", "main", *extra, cwd=self.repo)

    def table(self, branches: str) -> dict[str, list[str]]:
        proc = self.range_diff("--branches", branches)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = {}
        for line in proc.stdout.splitlines()[2:]:
            branch, *cells = line.split(None, 7)
            rows[branch] = cells
        return rows

    def test_classifies_each_pr(self) -> None:
        rows = self.table("a,b,c,e,d")
        self.assertEqual(rows["a"][0], rows["a"][1], "a keeps its head")
        self.assertEqual(rows["a"][2:], ["1", "0", "0", "0", "unchanged"])
        self.assertEqual(rows["b"][2:], ["0", "1", "0", "0", "rewritten 1"])
        self.assertEqual(rows["c"][2:], ["1", "0", "1", "0", "1 new"])
        self.assertEqual(rows["e"][2:6], ["1", "0", "0", "0"])
        self.assertRegex(rows["e"][6], r"^unchanged\s+moved from d onto c$")
        self.assertRegex(rows["d"][6], r"^unchanged\s+moved from c onto e$")

    def test_order_of_the_branch_list_does_not_change_parents(self) -> None:
        self.assertEqual(self.table("a,b,c,e,d"), self.table("a,b,c,d,e"))

    def test_backup_prefix_as_old_heads(self) -> None:
        for branch in ("a", "b", "c", "d", "e"):
            git(self.repo, "update-ref", f"refs/backup/wave/{branch}", f"origin/{branch}")
        git(self.repo, "update-ref", "refs/backup/wave/main", "origin/main")
        proc = self.range_diff("--branches", "a,b,c,e,d", "--old", "refs/backup/wave/")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rewritten 1", proc.stdout)

    def test_full_prints_one_range_diff(self) -> None:
        proc = self.range_diff("--branches", "a,b,c,e,d", "--full", "b")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout, r"1:\s+\w+ ! 1:\s+\w+ b: add b")
        self.assertIn("    -+b4\n    ++B4\n", proc.stdout)

    def test_markdown_details_only_changed_prs(self) -> None:
        proc = self.range_diff("--branches", "a,b,c,e,d", "--markdown")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("| `a` | ", proc.stdout)
        self.assertIn("<code>b</code>: rewritten 1</summary>", proc.stdout)
        self.assertIn("<code>c</code>: 1 new</summary>", proc.stdout)
        self.assertNotIn("<code>a</code>", proc.stdout)

    def test_reads_branches_and_pr_numbers_from_the_stack_record(self) -> None:
        stacks = Path(self.tmp.name) / "stacks"
        stacks.mkdir()
        prs = []
        parent, base_ref = "stack-base", "main"
        for number, branch in enumerate(("a", "b", "c", "e", "d"), start=11):
            prs.append(
                f'[[prs]]\nid = "PR-{branch}"\ntitle = "{branch}"\ntickets = ["ABC-{number}"]\nbranch = "{branch}"\n'
                f'parent = "{parent}"\nbase_ref = "{base_ref}"\ngithub_pr = {number}\nnotes = ""\n'
            )
            parent, base_ref = f"PR-{branch}", branch
        (stacks / "abc-1.toml").write_text(
            'version = 1\n\n[stack]\nid = "ABC-1"\ntitle = "t"\nbase_ref = "main"\n\n'
            f'[repo]\ngithub = "example/test"\nworkspace = "{self.repo}"\n\n' + "\n".join(prs)
        )
        (stacks / "abc-1.events.jsonl").touch()
        proc = run(
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(self.repo),
            "--stack",
            "ABC-1",
            "--stacks-dir",
            str(stacks),
            "--from",
            "b",
            cwd=self.repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = proc.stdout.splitlines()[2:]
        self.assertEqual([line.split()[0] for line in body], ["#12", "#13", "#14", "#15"])
        self.assertIn("rewritten 1", body[0])


class OlderCommitOfThePrBelowTest(unittest.TestCase):
    """Old heads where b sits on a's commit from before a was amended, and c on b's commit from before b grew.

    The rebuilt stack holds the same commits for b and c, so neither PR changed.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "work"
        git(Path(self.tmp.name), "init", "-q", "-b", "main", str(self.repo))
        commit_file(self.repo, "base.txt", "base\n", "base")
        git(self.repo, "switch", "-q", "-c", "a")
        commit_file(self.repo, "a.txt", lines("a1", "a2", "a3", "a4", "a5", "a6"), "a: add a")
        git(self.repo, "switch", "-q", "-c", "b")
        commit_file(self.repo, "b.txt", lines("b1", "b2"), "b: add b")
        git(self.repo, "switch", "-q", "-c", "c")
        commit_file(self.repo, "c.txt", lines("c1"), "c: add c")
        git(self.repo, "switch", "-q", "b")
        commit_file(self.repo, "b.txt", lines("b1", "b2", "b3"), "b: add b3")
        git(self.repo, "switch", "-q", "a")
        (self.repo / "a.txt").write_text(lines("a1", "a2", "A3", "a4", "a5", "a6"))
        git(self.repo, "commit", "-q", "-a", "--amend", "--no-edit")
        for branch in ("a", "b", "c"):
            git(self.repo, "update-ref", f"refs/backup/wave/{branch}", branch)

        git(self.repo, "rebase", "-q", "--onto", "a", "b~2", "b")
        git(self.repo, "rebase", "-q", "--onto", "b", "c~1", "c")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_compares_from_where_the_prs_own_commits_start(self) -> None:
        proc = run(
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(self.repo),
            "--base",
            "main",
            "--branches",
            "a,b,c",
            "--old",
            "refs/backup/wave/",
            cwd=self.repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = {line.split()[0]: line.split(None, 7)[3:] for line in proc.stdout.splitlines()[2:]}
        self.assertEqual(rows["a"], ["1", "0", "0", "0", "unchanged"])
        self.assertEqual(rows["b"][:4], ["2", "0", "0", "0"])
        self.assertRegex(rows["b"][4], r"^unchanged\s+old head was on an older commit of a$")
        self.assertEqual(rows["c"][:4], ["1", "0", "0", "0"])
        self.assertRegex(rows["c"][4], r"^unchanged\s+old head was on an older commit of b$")


if __name__ == "__main__":
    unittest.main()
