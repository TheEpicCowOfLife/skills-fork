"""stack-absorb: routing tip edits to the PRs that hold their lines, then one rebase.

Run from the skill directory: python3 -m unittest tests/test_stack_absorb.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "scripts" / "stack-absorb"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}
BRANCHES = ("ABC-2-one", "ABC-3-two", "ABC-4-three")


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, env=GIT_ENV, text=True, capture_output=True, check=False)


def git(cwd: Path, *args: str) -> str:
    proc = run("git", *args, cwd=cwd)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def numbered(prefix: str, count: int = 12) -> str:
    return "".join(f"{prefix} {number}\n" for number in range(1, count + 1))


def commit_all(cwd: Path, message: str) -> str:
    git(cwd, "add", "--all")
    git(cwd, "commit", "-q", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


def replace_line(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert f"{old}\n" in text, old
    path.write_text(text.replace(f"{old}\n", f"{new}\n"))


class AbsorbTest(unittest.TestCase):
    """notes.txt on main; ABC-2-one adds one.txt, ABC-3-two two.txt, ABC-4-three three.txt."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "wave"
        git(Path(self.tmp.name), "init", "-q", "-b", "main", str(self.repo))
        (self.repo / "notes.txt").write_text(numbered("main"))
        commit_all(self.repo, "base")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build_stack(self, two_commits: list[tuple[str, str]] | None = None) -> None:
        git(self.repo, "switch", "-q", "-c", BRANCHES[0])
        (self.repo / "one.txt").write_text(numbered("one"))
        commit_all(self.repo, "one: add one.txt")
        git(self.repo, "switch", "-q", "-c", BRANCHES[1])
        (self.repo / "two.txt").write_text(numbered("two"))
        commit_all(self.repo, "two: add two.txt")
        for content, message in two_commits or []:
            (self.repo / "one.txt").write_text(content)
            commit_all(self.repo, message)
        git(self.repo, "switch", "-q", "-c", BRANCHES[2])
        (self.repo / "three.txt").write_text(numbered("three"))
        commit_all(self.repo, "three: add three.txt")
        self.old = {branch: git(self.repo, "rev-parse", branch) for branch in BRANCHES}

    def absorb(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return run(sys.executable, str(SCRIPT), "--repo", str(self.repo), "--branches", ",".join(BRANCHES), *extra, cwd=self.repo)

    def make_edits(self, *, with_additions: bool = True) -> str:
        """Commit the edits on the top branch and return the edited tree."""
        replace_line(self.repo / "notes.txt", "main 6", "MAIN 6")
        replace_line(self.repo / "one.txt", "one 6", "ONE 6")
        replace_line(self.repo / "three.txt", "three 6", "THREE 6")
        if with_additions:
            (self.repo / "helper.txt").write_text("helper\n")
            with (self.repo / "two.txt").open("a") as handle:
                handle.write("two 13\n")
        commit_all(self.repo, "tip edits")
        return git(self.repo, "rev-parse", "HEAD^{tree}")

    def own_commits(self, base: str, branch: str) -> list[str]:
        return git(self.repo, "log", "--reverse", "--format=%H", f"{base}..{branch}").split()

    def changed_files(self, commit: str) -> list[str]:
        return sorted(git(self.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).split())

    def test_routes_each_hunk_and_rebuilds_the_stack(self) -> None:
        self.build_stack()
        edited_tree = self.make_edits()
        pins = ("--pin", "helper.txt=ABC-3-two", "--pin", "two.txt:13-13=ABC-3-two")

        dry = self.absorb("--edits", "HEAD", *pins)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        routing = {line.split()[1]: line for line in dry.stdout.splitlines()[2:7]}
        self.assertRegex(routing["notes.txt"], r"ABC-2-one\s+removes lines from \w{7}, in the bottom PR or below the stack")
        self.assertRegex(routing["one.txt"], r"ABC-2-one\s+removes lines from \w{7}, in the bottom PR or below the stack")
        self.assertRegex(routing["three.txt"], rf"ABC-4-three\s+removes lines from {self.old['ABC-4-three'][:7]} in ABC-4-three")
        self.assertIn("pinned by --pin helper.txt=ABC-3-two", routing["helper.txt"])
        self.assertIn("pinned by --pin two.txt:13-13=ABC-3-two", routing["two.txt"])
        self.assertIn("Simulation: OK: the rebuilt tip matches the edited tree.", dry.stdout)
        self.assertEqual({branch: git(self.repo, "rev-parse", branch) for branch in BRANCHES[:2]}, {b: self.old[b] for b in BRANCHES[:2]})

        applied = self.absorb("--edits", "HEAD", *pins, "--apply", "--message", "[{ticket}] Tidy {branch}")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertIn("ABC-4-three matches the edited tree.", applied.stdout)

        new = {branch: git(self.repo, "rev-parse", branch) for branch in BRANCHES}
        base = git(self.repo, "rev-parse", "main")
        expected_files = {
            "ABC-2-one": ["notes.txt", "one.txt"],
            "ABC-3-two": ["helper.txt", "two.txt"],
            "ABC-4-three": ["three.txt"],
        }
        parents = {"ABC-2-one": base, "ABC-3-two": new["ABC-2-one"], "ABC-4-three": new["ABC-3-two"]}
        for branch, ticket in zip(BRANCHES, ("ABC-2", "ABC-3", "ABC-4")):
            commits = self.own_commits(parents[branch], branch)
            self.assertEqual(len(commits), 2, f"{branch} keeps its commit and gains exactly one")
            self.assertEqual(git(self.repo, "log", "-1", "--format=%s", commits[-1]), f"[{ticket}] Tidy {branch}")
            self.assertEqual(self.changed_files(commits[-1]), expected_files[branch])
            self.assertEqual(git(self.repo, "rev-parse", f"{commits[-1]}^"), git(self.repo, "rev-parse", f"{commits[0]}"))
        self.assertEqual(self.own_commits(base, "ABC-2-one")[0], self.old["ABC-2-one"], "the bottom PR's own commit is untouched")
        self.assertTrue(run("git", "merge-base", "--is-ancestor", new["ABC-2-one"], new["ABC-3-two"], cwd=self.repo).returncode == 0)
        self.assertEqual(git(self.repo, "rev-parse", "ABC-4-three^{tree}"), edited_tree)
        self.assertNotIn("tip edits", git(self.repo, "log", "--format=%s", "ABC-4-three"))

    def test_unpinned_pure_additions_are_refused(self) -> None:
        self.build_stack()
        self.make_edits()
        git(self.repo, "switch", "-q", "--detach")
        git(self.repo, "branch", "-q", "-f", "ABC-4-three", "HEAD^")

        proc = self.absorb("--edits", "HEAD", "--apply", "--message", "[{ticket}] Tidy")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("Refused:", proc.stdout)
        self.assertIn("hunk 1 helper.txt -0,0 +1,1: adds lines only, so it has no preimage to route by", proc.stdout)
        self.assertIn("pin it with --pin helper.txt:1-1=<pr>", proc.stdout)
        self.assertIn("hunk 5 two.txt -10,3 +10,4: adds lines only", proc.stdout)
        self.assertIn("pin it with --pin two.txt:13-13=<pr>", proc.stdout)
        self.assertEqual({branch: git(self.repo, "rev-parse", branch) for branch in BRANCHES}, self.old)

    def test_a_conflict_stops_the_rebase_and_says_where(self) -> None:
        with_x = numbered("one").replace("one 6\n", "one 6\nX\n")
        self.build_stack([(with_x, "two: insert X"), (numbered("one"), "two: remove X")])
        replace_line(self.repo / "one.txt", "one 6", "ONE 6")
        commit_all(self.repo, "tip edits")

        dry = self.absorb("--edits", "HEAD")
        self.assertEqual(dry.returncode, 1, dry.stdout + dry.stderr)
        self.assertRegex(dry.stdout, r"Simulation: CONFLICT replaying \w{7} two: insert X onto the rebuilt ABC-3-two, in one.txt")

        applied = self.absorb("--edits", "HEAD", "--apply", "--message", "[{ticket}] Shout")
        self.assertEqual(applied.returncode, 1, applied.stdout)
        self.assertRegex(applied.stderr, r"The rebase stopped while rebuilding ABC-3-two, at \w{7} two: insert X\.")
        self.assertIn("Conflicting files: one.txt.", applied.stderr)
        git(self.repo, "rebase", "--abort")
        self.assertEqual(git(self.repo, "rev-parse", "ABC-2-one"), self.old["ABC-2-one"])

    def test_a_replay_continues_past_conflicts_rerere_resolved(self) -> None:
        git(self.repo, "config", "rerere.enabled", "true")
        git(self.repo, "config", "rerere.autoUpdate", "true")
        with_x = numbered("one").replace("one 6\n", "one 6\nX\n")
        self.build_stack([(with_x, "two: insert X"), (numbered("one"), "two: remove X")])
        replace_line(self.repo / "one.txt", "one 6", "ONE 6")
        edits = commit_all(self.repo, "tip edits")
        edited_tree = git(self.repo, "rev-parse", f"{edits}^{{tree}}")

        first = self.absorb("--edits", edits, "--apply", "--message", "[{ticket}] Shout")
        self.assertEqual(first.returncode, 1, first.stdout)
        self.assertIn("Conflicting files: one.txt.", first.stderr)
        resolutions = [(with_x.replace("one 6\n", "ONE 6\n"), True), (numbered("one").replace("one 6\n", "ONE 6\n"), False)]
        for resolution, stops_again in resolutions:
            (self.repo / "one.txt").write_text(resolution)
            git(self.repo, "add", "one.txt")
            proc = run("git", "-c", "core.editor=true", "rebase", "--continue", cwd=self.repo)
            self.assertEqual(proc.returncode != 0, stops_again, proc.stdout + proc.stderr)
        trees = {branch: git(self.repo, "rev-parse", f"{branch}^{{tree}}") for branch in BRANCHES}

        git(self.repo, "switch", "-q", "--detach")
        for branch in BRANCHES[:2]:
            git(self.repo, "branch", "-q", "-f", branch, self.old[branch])
        git(self.repo, "branch", "-q", "-f", BRANCHES[2], edits)
        second = self.absorb("--edits", edits, "--apply", "--message", "[{ticket}] Shout")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertRegex(
            second.stdout,
            r"rerere resolved:\n"
            r"  one.txt at \w{7} two: insert X, rebuilding ABC-3-two\n"
            r"  one.txt at \w{7} two: remove X, rebuilding ABC-3-two\n",
        )
        self.assertEqual({branch: git(self.repo, "rev-parse", f"{branch}^{{tree}}") for branch in BRANCHES}, trees)
        self.assertEqual(trees[BRANCHES[2]], edited_tree)

    def test_context_decides_how_low_a_hunk_can_go(self) -> None:
        changed_by_two = numbered("one").replace("one 3\n", "one 3 (two)\n").replace("one 12\n", "one 12 (two)\n")
        self.build_stack([(changed_by_two, "two: annotate one.txt")])
        replace_line(self.repo / "one.txt", "one 2", "ONE 2")
        replace_line(self.repo / "one.txt", "one 10", "ONE 10")
        edited_tree = git(self.repo, "rev-parse", f"{commit_all(self.repo, 'tip edits')}^{{tree}}")

        dry = self.absorb("--edits", "HEAD")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        rows = dry.stdout.splitlines()[2:4]
        self.assertRegex(rows[0], r"-1,5 \+1,5\s+ABC-3-two\s+removes lines from \w{7}, .*; its context first exists in ABC-3-two$")
        self.assertRegex(rows[1], r"-7,6 \+7,6\s+ABC-2-one\s+removes lines from \w{7}, .*; applies with reduced context$")

        applied = self.absorb("--edits", "HEAD", "--apply", "--message", "[{ticket}] Shout")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "ABC-4-three^{tree}"), edited_tree)
        self.assertIn("ONE 10", git(self.repo, "show", "ABC-2-one:one.txt"))
        self.assertNotIn("ONE 2", git(self.repo, "show", "ABC-2-one:one.txt"))

    def test_only_the_top_pr_changes(self) -> None:
        self.build_stack()
        replace_line(self.repo / "three.txt", "three 6", "THREE 6")
        git(self.repo, "switch", "-q", "--detach")
        edited_tree = git(self.repo, "rev-parse", f"{commit_all(self.repo, 'tip edits')}^{{tree}}")

        applied = self.absorb("--edits", "HEAD", "--apply", "--message", "[{ticket}] Shout")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "ABC-3-two"), self.old["ABC-3-two"])
        self.assertEqual(git(self.repo, "rev-parse", "ABC-4-three^"), self.old["ABC-4-three"])
        self.assertEqual(git(self.repo, "rev-parse", "ABC-4-three^{tree}"), edited_tree)

    def test_refuses_a_branch_checked_out_in_another_worktree(self) -> None:
        self.build_stack()
        self.make_edits(with_additions=False)
        git(self.repo, "worktree", "add", "-q", str(Path(self.tmp.name) / "elsewhere"), "ABC-3-two")
        proc = self.absorb("--edits", "HEAD", "--apply", "--message", "x")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cannot move a branch checked out in another worktree: ABC-3-two", proc.stderr)


if __name__ == "__main__":
    unittest.main()
