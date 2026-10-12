---
name: list-pr-stacks
description: List the PR stack records kept by manage-pr-stack, ordered from most recent activity to least recent. Use when the user asks which PR stacks they have, to list or show their stacks, what stacks are active or stale, or which stack they worked on last. Read-only; hand off to manage-pr-stack for anything that inspects one stack in depth or changes it.
---

# List PR Stacks

Companion to `manage-pr-stack`. Lists every stack record in the `stacks/` directory of the `manage-pr-stack`
skill installed next to this one, most recently active first.

## Run

Run the script by this skill's directory path as installed, without resolving symlinks, so it finds the sibling
`manage-pr-stack`:

```bash
python3 <this-skill-dir>/list_stacks.py
```

Options:

- `--limit N`: only the N most recently active stacks. Use it when the user asks for "recent" or "latest" stacks.
- `--repo owner/name`: only stacks for one GitHub repo.
- `--json`: full records, including the whole last event (`pr_ids`, `notes`) and the TOML path.

## Ordering

Activity is the `date` of the last line in the stack's `*.events.jsonl`. Event dates are day-granular, so stacks
active on the same day are ordered by the later modification time of the stack's TOML and event log. A stack with
no events falls back to that modification date.

## Presenting

- Show the script's Markdown table as is. Do not reorder it or drop columns.
- After the table, add at most one line calling out anything notable, such as several stacks with no activity in
  over a month. Do not speculate about whether a stack is merged or abandoned.
- The list comes from local records only. Do not query GitHub, Jira or git to decorate it. If the user wants live
  state for a stack (PR status, CI, whether branches still exist), switch to `manage-pr-stack` for that stack.
- If the user picks a stack from the list, continue with `manage-pr-stack` (`python3 stack.py show <id>`).

## Failure

The script shells out to `manage-pr-stack/stack.py --json list`. If that fails, report the error output as is;
an invalid stack record is fixed with `manage-pr-stack`, not here.
