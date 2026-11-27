"""stack-push: base-change ordering, refusals, and the generated script run against a local origin.

A fake gh on PATH keeps PR bases in a JSON file. It refuses a base change that
would put the PR's head inside its base, and a post-receive hook on the bare
origin marks a PR merged whenever a push leaves its head inside its base, which
is what GitHub does.

Run from the skill directory: python3 -m unittest tests/test_stack_push.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "scripts" / "stack-push"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

FAKE_GH = """\
import json, os, subprocess, sys

state_path, log_path = os.environ["FAKE_GH_STATE"], os.environ["FAKE_GH_LOG"]
with open(state_path) as handle:
    state = json.load(handle)


def save():
    with open(state_path, "w") as handle:
        json.dump(state, handle)


def origin_oid(branch):
    proc = subprocess.run(
        ["git", "--git-dir", state["origin"], "rev-parse", "--verify", "--quiet", "refs/heads/" + branch],
        capture_output=True, text=True,
    )
    return proc.stdout.strip() or None


def contains(base, head):
    base_oid, head_oid = origin_oid(base), origin_oid(head)
    if not base_oid or not head_oid:
        return False
    return subprocess.run(["git", "--git-dir", state["origin"], "merge-base", "--is-ancestor", head_oid, base_oid]).returncode == 0


args = sys.argv[1:]
if args[:2] == ["pr", "list"]:
    open_prs = [
        {"number": int(number), "headRefName": pr["head"], "baseRefName": pr["base"]}
        for number, pr in state["prs"].items()
        if number not in state["merged"]
    ]
    print(json.dumps(open_prs))
    sys.exit(0)
if args[0] != "api":
    sys.exit("fake gh: unsupported " + " ".join(args))
rest = args[1:]
method = "GET"
if rest[:2] == ["-X", "PATCH"]:
    method, rest = "PATCH", rest[2:]
number = rest[0].rsplit("/", 1)[1]
pr = state["prs"][number]
if method == "PATCH":
    base = next(arg.split("=", 1)[1] for arg in rest if arg.startswith("base="))
    with open(log_path, "a") as handle:
        handle.write(f"PATCH #{number} base={base} head={origin_oid(pr['head'])}\\n")
    if pr.get("stack"):
        sys.exit("cannot change the base of a PR that is part of a stack")
    if contains(base, pr["head"]):
        state["merged"].append(number)
        save()
        sys.exit("base contains head: PR marked merged")
    pr["base"] = base
    save()
    sys.exit(0)
data = {"number": int(number), "base": {"ref": pr["base"]}, "head": {"ref": pr["head"]}}
if "stack" in pr:
    data["stack"] = pr["stack"]
if "--jq" in rest:
    for key in rest[rest.index("--jq") + 1].lstrip(".").split("."):
        data = data[key]
    print(data)
else:
    print(json.dumps(data))
"""

POST_RECEIVE = """\
import json, subprocess, sys

state_path, log_path = {state!r}, {log!r}
updates = sys.stdin.read().split()
with open(log_path, "a") as handle:
    handle.write("PUSH " + " ".join(sorted(updates[2::3])) + "\\n")
with open(state_path) as handle:
    state = json.load(handle)


def oid(ref):
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], capture_output=True, text=True).stdout.strip()


for number, pr in state["prs"].items():
    head, base = oid("refs/heads/" + pr["head"]), oid("refs/heads/" + pr["base"])
    if number not in state["merged"] and head and base:
        if subprocess.run(["git", "merge-base", "--is-ancestor", head, base]).returncode == 0:
            state["merged"].append(number)
with open(state_path, "w") as handle:
    json.dump(state, handle)
"""


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


def write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o755)


class PushTest(unittest.TestCase):
    """PRs #1 a, #2 b, #3 c, #4 d stacked on main, pushed to a bare origin."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin.git"
        self.repo = self.root / "wave"
        git(self.root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(self.root, "clone", "-q", str(self.origin), str(self.repo))
        commit_file(self.repo, "base.txt", "base\n", "base")
        git(self.repo, "push", "-q", "origin", "HEAD:main")
        parent = "main"
        for branch in ("a", "b", "c", "d"):
            git(self.repo, "switch", "-q", "-c", branch, parent)
            commit_file(self.repo, f"{branch}.txt", f"{branch}\n", branch)
            parent = branch
        git(self.repo, "push", "-q", "origin", "a", "b", "c", "d")
        git(self.repo, "switch", "-q", "main")
        self.old = {branch: git(self.repo, "rev-parse", branch) for branch in ("a", "b", "c", "d")}

        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        write_executable(bin_dir / "gh", FAKE_GH)
        self.state = self.root / "gh-state.json"
        self.log = self.root / "calls.log"
        self.state.write_text(
            json.dumps(
                {
                    "origin": str(self.origin),
                    "merged": [],
                    "prs": {
                        "1": {"head": "a", "base": "main"},
                        "2": {"head": "b", "base": "a"},
                        "3": {"head": "c", "base": "b"},
                        "4": {"head": "d", "base": "c"},
                    },
                }
            )
        )
        write_executable(
            self.origin / "hooks" / "post-receive", POST_RECEIVE.format(state=str(self.state), log=str(self.log))
        )
        self.env = {
            **GIT_ENV,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_STATE": str(self.state),
            "FAKE_GH_LOG": str(self.log),
        }
        self.script = self.root / "push.sh"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def move_d_above_a(self) -> None:
        """New order a, d, b, c."""
        git(self.repo, "rebase", "-q", "--onto", "a", "c", "d")
        git(self.repo, "rebase", "-q", "--onto", "d", "a", "b")
        git(self.repo, "rebase", "-q", "--onto", "b", "origin/b", "c")
        git(self.repo, "switch", "-q", "main")

    def generate(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return run(
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(self.repo),
            "--slug",
            "example/test",
            "--output",
            str(self.script),
            *extra,
            cwd=self.repo,
            env=self.env,
        )

    def summary(self, proc: subprocess.CompletedProcess[str]) -> dict[str, str]:
        body = proc.stdout.split("\n\n")[0].splitlines()[2:]
        return {line.split()[0]: line for line in body}

    def gh_state(self) -> dict:
        return json.loads(self.state.read_text())

    def origin_head(self, branch: str) -> str:
        return git(self.origin, "rev-parse", f"refs/heads/{branch}")

    def test_reorder_orders_base_changes_around_the_push(self) -> None:
        self.move_d_above_a()
        proc = self.generate("--branches", "a,d,b,c")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rows = self.summary(proc)
        self.assertRegex(rows["a"], r"\bno\s+main \(unchanged\)$")
        self.assertIn("c -> a (before push)", rows["d"])
        self.assertIn("a -> d (after push)", rows["b"])
        self.assertIn("b (unchanged)", rows["c"])

        script = self.script.read_text()
        self.assertIn("set -euo pipefail", script)
        self.assertIn(f"--force-with-lease=refs/heads/d:{self.old['d']}", script)
        self.assertNotIn("refs/heads/a:", script)

        ran = run("bash", str(self.script), cwd=self.root, env=self.env)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("Read-back OK", ran.stdout)
        calls = self.log.read_text().splitlines()
        self.assertEqual(
            calls,
            [
                f"PATCH #4 base=a head={self.old['d']}",
                "PUSH refs/heads/b refs/heads/c refs/heads/d",
                f"PATCH #2 base=d head={git(self.repo, 'rev-parse', 'b')}",
            ],
        )
        state = self.gh_state()
        self.assertEqual(state["merged"], [])
        self.assertEqual({number: pr["base"] for number, pr in state["prs"].items()}, {"1": "main", "2": "d", "3": "b", "4": "a"})
        for branch in ("a", "b", "c", "d"):
            self.assertEqual(self.origin_head(branch), git(self.repo, "rev-parse", branch))

    def test_refuses_when_neither_order_is_safe(self) -> None:
        git(self.repo, "rebase", "-q", "--onto", "main", "origin/b", "c")
        git(self.repo, "rebase", "-q", "--onto", "c", "origin/a", "b")
        git(self.repo, "rebase", "-q", "--onto", "b", "main", "a")
        git(self.repo, "switch", "-q", "main")
        proc = self.generate("--branches", "c,b,a", "--base", "main")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("#2 (b): base a -> c is unsafe on both sides of the push", proc.stderr)
        self.assertNotIn("#1 (a)", proc.stderr)
        self.assertNotIn("#3 (c)", proc.stderr)
        self.assertFalse(self.script.exists())

    def test_refuses_a_pr_in_a_github_stack(self) -> None:
        state = self.gh_state()
        state["prs"]["4"]["stack"] = {"id": 4174155, "number": 6143, "position": 3, "size": 3, "base": {"ref": "main"}}
        self.state.write_text(json.dumps(state))
        self.move_d_above_a()
        proc = self.generate("--branches", "a,d,b,c")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("#4 (d): the PR is part of a GitHub stack", proc.stderr)
        self.assertFalse(self.script.exists())

    def test_a_null_or_missing_stack_field_is_not_a_github_stack(self) -> None:
        state = self.gh_state()
        state["prs"]["4"]["stack"] = None
        self.state.write_text(json.dumps(state))
        self.move_d_above_a()
        proc = self.generate("--branches", "a,d,b,c")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("c -> a (before push)", self.summary(proc)["d"])
        self.assertIn("a -> d (after push)", self.summary(proc)["b"])

    def test_stale_lease_stops_the_script_before_anything_is_pushed(self) -> None:
        self.move_d_above_a()
        self.assertEqual(self.generate("--branches", "a,d,b,c").returncode, 0)
        other = self.root / "other"
        git(self.root, "clone", "-q", str(self.origin), str(other))
        git(other, "switch", "-q", "c")
        commit_file(other, "late.txt", "late\n", "someone else's commit")
        git(other, "push", "-q", "origin", "c")
        late_c = git(other, "rev-parse", "HEAD")
        self.log.write_text("")

        ran = run("bash", str(self.script), cwd=self.root, env=self.env)
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("The push failed, so nothing changed on origin.", ran.stderr)
        self.assertIn("gh api -X PATCH repos/example/test/pulls/4 -f base=c", ran.stderr)
        self.assertEqual(self.origin_head("b"), self.old["b"])
        self.assertEqual(self.origin_head("c"), late_c)
        self.assertEqual(self.origin_head("d"), self.old["d"])
        self.assertNotIn("#2", self.log.read_text())

    def test_sync_workspace_moves_only_branches_still_at_their_old_heads(self) -> None:
        workspace = self.root / "workspace"
        git(self.root, "clone", "-q", str(self.origin), str(workspace))
        for branch in ("b", "c", "d"):
            git(workspace, "branch", "-q", branch, f"origin/{branch}")
        git(workspace, "worktree", "add", "-q", str(self.root / "workspace-b"), "b")
        git(workspace, "switch", "-q", "d")
        commit_file(workspace, "local.txt", "local\n", "local work on d")
        git(workspace, "switch", "-q", "main")
        local_d = git(workspace, "rev-parse", "d")

        self.move_d_above_a()
        proc = self.generate("--branches", "a,d,b,c", "--sync-workspace", str(workspace))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        ran = run("bash", str(self.script), cwd=self.root, env=self.env)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

        self.assertIn(f"MOVED  b in {self.root / 'workspace-b'}", ran.stdout)
        self.assertIn("MOVED  c", ran.stdout)
        self.assertIn(f"SKIP   d: the workspace branch is at {local_d}, not {self.old['d']}", ran.stdout)
        self.assertEqual(git(self.root / "workspace-b", "rev-parse", "HEAD"), git(self.repo, "rev-parse", "b"))
        self.assertEqual(git(self.root / "workspace-b", "status", "--porcelain"), "")
        self.assertEqual(git(workspace, "rev-parse", "c"), git(self.repo, "rev-parse", "c"))
        self.assertEqual(git(workspace, "rev-parse", "d"), local_d)


if __name__ == "__main__":
    unittest.main()
