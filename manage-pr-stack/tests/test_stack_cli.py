import json
import os
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STACK_PY = ROOT / "stack.py"


class StackCliTest(unittest.TestCase):
    def run_cli(self, stacks_dir: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(STACK_PY), "--stacks-dir", str(stacks_dir), *args],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def run_ok(self, stacks_dir: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        result = self.run_cli(stacks_dir, *args, env=env)
        if result.returncode != 0:
            self.fail(f"command failed: {result.stderr}")
        return result

    def create_stack(self, stacks_dir: Path) -> Path:
        self.run_ok(
            stacks_dir,
            "add-stack",
            "--id",
            "ABC-123",
            "--title",
            "Planned stack",
            "--github",
            "example-org/todo-app",
            "--workspace",
            str(stacks_dir),
            "--base-ref",
            "main",
            "--filename",
            "abc-123.toml",
        )
        return stacks_dir / "abc-123.toml"

    def test_add_to_stack_without_github_pr_creates_planned_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stacks_dir = Path(tmp)
            stack_path = self.create_stack(stacks_dir)

            self.run_ok(
                stacks_dir,
                "add-to-stack",
                "ABC-123",
                "--title",
                "Add todo API",
                "--ticket",
                "ABC-124",
                "--branch",
                "user/abc-124-todo-api",
            )

            self.run_ok(stacks_dir, "validate", "ABC-123")
            data = tomllib.loads(stack_path.read_text())
            self.assertNotIn("github_pr", data["prs"][0])

            show = self.run_ok(stacks_dir, "--json", "show", "ABC-123")
            pr = json.loads(show.stdout)["prs"][0]
            self.assertIsNone(pr["github_pr"])
            self.assertEqual(pr["github"], "")

            tip = self.run_ok(stacks_dir, "tip", "ABC-123")
            self.assertEqual(tip.stdout.strip(), "ABC-124\tuser/abc-124-todo-api")

    def test_import_pr_updates_matching_planned_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stacks_dir = Path(tmp)
            stack_path = self.create_stack(stacks_dir)
            self.run_ok(
                stacks_dir,
                "add-to-stack",
                "ABC-123",
                "--title",
                "Add todo API",
                "--ticket",
                "ABC-124",
                "--branch",
                "user/abc-124-todo-api",
            )

            bin_dir = stacks_dir / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import sys

                    json.dump(
                        {
                            "number": 42,
                            "title": "ABC-124 Add todo API",
                            "headRefName": "user/abc-124-todo-api",
                            "baseRefName": "main",
                        },
                        sys.stdout,
                    )
                    """
                )
            )
            gh.chmod(0o755)
            env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

            self.run_ok(stacks_dir, "import-pr", "ABC-123", "42", env=env)
            self.run_ok(stacks_dir, "validate", "ABC-123")

            data = tomllib.loads(stack_path.read_text())
            self.assertEqual(data["prs"][0]["github_pr"], 42)

    def test_duplicate_missing_github_pr_values_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stacks_dir = Path(tmp)
            self.create_stack(stacks_dir)

            self.run_ok(
                stacks_dir,
                "add-to-stack",
                "ABC-123",
                "--title",
                "Add todo API",
                "--ticket",
                "ABC-124",
                "--branch",
                "user/abc-124-todo-api",
            )
            self.run_ok(
                stacks_dir,
                "add-to-stack",
                "ABC-123",
                "--title",
                "Add todo UI",
                "--ticket",
                "ABC-125",
                "--branch",
                "user/abc-125-todo-ui",
            )
            self.run_ok(stacks_dir, "validate", "ABC-123")

    def test_duplicate_present_github_pr_values_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stacks_dir = Path(tmp)
            self.create_stack(stacks_dir)

            self.run_ok(
                stacks_dir,
                "add-to-stack",
                "ABC-123",
                "--title",
                "Add todo API",
                "--ticket",
                "ABC-124",
                "--branch",
                "user/abc-124-todo-api",
                "--github-pr",
                "42",
            )
            result = self.run_cli(
                stacks_dir,
                "add-to-stack",
                "ABC-123",
                "--title",
                "Add todo UI",
                "--ticket",
                "ABC-125",
                "--branch",
                "user/abc-125-todo-ui",
                "--github-pr",
                "42",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("GitHub PR already exists in stack: 42", result.stderr)


if __name__ == "__main__":
    unittest.main()
