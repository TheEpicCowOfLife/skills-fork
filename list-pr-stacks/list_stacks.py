#!/usr/bin/env python3
"""List manage-pr-stack records, most recent activity first."""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from textwrap import shorten

# Not resolved: when this skill is symlinked into a skills directory, manage-pr-stack is a sibling of the link.
STACK_CLI = Path(__file__).absolute().parent.parent / "manage-pr-stack" / "stack.py"
SUMMARY_WIDTH = 100


def read_last_event(events_path: Path) -> dict | None:
    if not events_path.exists():
        return None
    last = None
    for line in events_path.read_text().splitlines():
        if line.strip():
            last = json.loads(line)
    return last


def collect_stacks() -> list[dict]:
    if not STACK_CLI.exists():
        sys.exit(f"manage-pr-stack not found at {STACK_CLI}; install it next to list-pr-stacks")
    listed = subprocess.run(
        [sys.executable, str(STACK_CLI), "--json", "list"],
        check=True,
        capture_output=True,
        text=True,
    )
    stacks = []
    for stack in json.loads(listed.stdout):
        toml_path = Path(stack["path"])
        events_path = toml_path.with_name(f"{toml_path.stem}.events.jsonl")
        last_event = read_last_event(events_path)
        modified = max(p.stat().st_mtime for p in (toml_path, events_path) if p.exists())
        stacks.append(
            {
                **stack,
                "last_event": last_event,
                # Event dates are day-granular, so file mtime orders stacks touched on the same day.
                "last_activity_date": last_event["date"] if last_event else datetime.fromtimestamp(modified).date().isoformat(),
                "last_modified": datetime.fromtimestamp(modified).isoformat(timespec="minutes"),
            }
        )
    stacks.sort(key=lambda x: (x["last_activity_date"], x["last_modified"]), reverse=True)
    return stacks


def print_table(stacks: list[dict]) -> None:
    print("| # | Last activity | Stack | Title | Repo | PRs | Tip branch | Last event |")
    print("|---|---|---|---|---|---|---|---|")
    for i, stack in enumerate(stacks, start=1):
        event = stack["last_event"]
        event_text = f"{event['kind']}: {shorten(event['summary'], SUMMARY_WIDTH)}" if event else "(no events)"
        cells = [
            str(i),
            stack["last_activity_date"],
            stack["id"],
            stack["title"],
            stack["repo"],
            str(stack["prs"]),
            f"`{stack['tip']}`",
            event_text,
        ]
        print("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="show only the N most recently active stacks")
    parser.add_argument("--repo", help="only stacks for this GitHub owner/name")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a Markdown table")
    args = parser.parse_args()

    stacks = collect_stacks()
    if args.repo:
        stacks = [x for x in stacks if x["repo"] == args.repo]
    if args.limit:
        stacks = stacks[: args.limit]

    if args.json:
        print(json.dumps(stacks, indent=2))
    else:
        print_table(stacks)


if __name__ == "__main__":
    main()
