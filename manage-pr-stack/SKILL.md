---
name: manage-pr-stack
description: Manage PR stacks. Use when the user mentions a PR stack, stacked PRs, a Jira story used as a stack identifier, stack records, descendants, rebasing a stack, force-pushing stack branches, importing GitHub PRs into a stack, or updating stack state across GitHub, Jira, local git, and durable stack files.
---

# Manage PR Stack

## Overview

Use this skill to coordinate stacked PR work through structured stack records in this skill directory. The canonical records live in `stacks/*.toml`, with append-only event history in matching `stacks/*.events.jsonl` files.

The stack files are durable coordination state, not a live mirror of GitHub, Jira, or local git. GitHub, Jira, and local git remain authoritative for current external facts when a requested operation depends on them.

Use `stack.py` as the normal interface for reading and updating stack records. Direct TOML or JSONL edits are a fallback for careful repairs that the CLI cannot express.

## Package Files

- `stack.py`: CLI for stack discovery, inspection, validation, topology updates, event logging and reading, and GitHub PR import.
- `docs/storage-format.md`: TOML stack and JSONL event-log format reference.
- `docs/cli-reference.md`: command reference and examples for `stack.py`.
- `stacks/*.toml`: ignored local stack records for real current work.
- `stacks/*.events.jsonl`: ignored local event logs for real current work.
- `examples/*.toml`: tracked example stack records.
- `examples/*.events.jsonl`: tracked example event logs.
- `scripts/stack-rebase`: helper for dry-running, rebasing, and force-pushing linear PR stack branches with explicit `--onto` boundaries and `--force-with-lease`. Its preflight refuses a boundary that would replay commits already in the new parent.
- `scripts/stack-absorb`: routes edits made at the stack tip, given as one commit, to the lowest PR whose head already holds each hunk's preimage, found with `git blame`. Hunks that only add lines need `--pin`. The dry run prints the routing and replays the stack to check the result; `--apply` writes one commit per PR and runs one `git rebase -i --update-refs`, continuing past any stop where rerere resolved every conflict.
- `scripts/stack-range-diff`: runs `git range-diff` for every PR between old heads (`origin/*` or a backup ref prefix) and new local heads, and labels each PR unchanged, rewritten, gaining commits or losing commits. It finds each PR's parent from ancestry, so reorders need no extra input. `--markdown` prints a summary to send to a reviewer.
- `scripts/stack-verify`: runs check commands on each stack head in turn, in one reused detached worktree. It caches passing results by tree and command list, re-runs a setup command only when its input files change, keeps a log per head, and exits non-zero on any failure.
- `scripts/stack-push`: writes a bash script that pushes rebuilt stack branches in one `git push --atomic` with a `--force-with-lease` per branch, changes PR bases with `gh api` before or after the push so GitHub never sees a PR's head inside its base, and reads every head and base back. It pushes nothing itself; the user runs the script.
- `tests/`: unittests for the CLI and the scripts; run `python3 -m unittest discover -s tests` from the skill directory after changing `stack.py` or anything in `scripts/`.

## Core Rules

- Prefer `stack.py` for every stack read or write. It understands selectors, validates topology, appends events, and keeps TOML rendering consistent. See `docs/cli-reference.md` for command details.
- Preserve real stack data under `stacks/`. That directory is ignored by git because it contains the user's current local state.
- Do not store transient execution plans in stack files. Store current topology in TOML and durable history in the event log. See `docs/storage-format.md` for the storage contract.
- Do not add compatibility layers, legacy fields, or references to previous formats unless the user explicitly asks.
- Do not refresh external state on first read without a concrete reason. Refresh GitHub, Jira, origin, or local git when the user's request depends on current state, local refs are stale, topology is ambiguous, or a remote operation is about to be performed.
- A direct command such as "rebase the PR for DEL-1878 and rebase the rest of the stack" counts as approval to fetch, inspect current remote state, rebase affected branches, resolve straightforward conflicts, and force-push rebased branches when that is necessary to complete the requested stack operation.
- A direct command such as "I've just pushed a minor adjustment to del-1876; update the PR stack" means propagate the parent branch change through descendants: fetch, identify descendants, rebase in order, resolve straightforward conflicts, force-push rebased descendants, and record what changed.
- Treat "update the PR stack" as an operational request to make the stack current. Treat "update the stack record", "update the stack file", or "record this" as a record-only request.
- Stop and ask before continuing if a rebase conflict is non-trivial, the worktree is dirty with unrelated changes, observed branch topology differs from the stack record, the operation would affect branches outside the requested stack, or the command's intent is ambiguous.
- Before rebuilding several branches of a stack at once (a restack wave), run `python3 stack.py events <stack> --kind claim --kind push --kind abandon`. If the last of these is a `claim`, another session is building a wave: coordinate with whoever its summary names before you build. Otherwise record your own claim first with `record-event --kind claim`, naming yourself and the PRs in scope. Release it with a `push` event once the wave is pushed, or an `abandon` event if you drop it.
- Do not transition Jira, request reviewers, change GitHub PR bases, merge PRs, or post Slack messages unless the user explicitly requests that external write.
- Use the existing workflow skills for phase-specific work: `implement-ticket`, `raise-my-pr`, `review-pr-feedback`, and `prepare-review-request`.

## Common Workflows

### Locate A Stack

1. Extract Jira story keys, implementation ticket IDs, PR numbers, PR URLs, and branch names from the request.
2. Use `python3 stack.py find <query>` with the most specific identifier first.
3. If exactly one stack matches, use it.
4. If multiple stacks match, show candidates and ask the user to choose.
5. If no stack exists and the user asked to create one, use `add-stack`. Otherwise ask whether to create it.

### Read Stack Context

1. Use `show` for topology and PR URLs when present.
2. Use `tip` when deciding where a new PR should be based.
3. Use `descendants` when a parent changed, merged, or needs rebasing.
4. Use `validate` before acting on topology.
5. Query GitHub, Jira, origin, or local git only when current external facts matter.

### Add Or Update A PR

1. Prefer `import-pr` when a GitHub PR already exists and `gh` is available.
2. Use `add-to-stack` when the user supplies the needed PR identity and branch. Include `--github-pr` only when a GitHub PR already exists.
3. Use `--parent` and `--base-ref` only for non-tip insertion or explicit topology repairs.
4. Validate after mutation.
5. Do not add Jira status, reviewers, CI state, merge commits, branch deletion state, or workflow phase fields.

### Mark A Parent Merged

1. Confirm whether the user wants a record-only update or actual GitHub/local branch work.
2. If record-only, use `mark-merged <stack> <target> --base <ref>`.
3. If descendants need rebasing, use `descendants` and `rebase-plan --repo <workspace>`, then perform the stack rebase workflow if authorized.
4. Record an event describing completed durable work.

### Propagate A Pushed Parent Change

Use this when the user says a branch was pushed or adjusted and asks to update the PR stack.

1. Locate and validate the stack.
2. Fetch origin and inspect local/remote refs for the changed branch and descendants.
3. Identify descendants with `descendants`.
4. Generate a `rebase-plan` from the changed PR onto the requested or inferred parent. Pass `--repo <workspace>` so a stack-base boundary resolves to the merge-base with the remote base, never the possibly stale local base branch.
5. Dry-run `scripts/stack-rebase` and check each branch replays only its own commits. If preflight refuses a boundary, use the merge-base it prints as the first row's third column after confirming it is the commit the branch was built on.
6. Rebase each descendant in order and force-push because the user requested stack propagation.
7. Stop and ask if conflicts are non-trivial, a descendant branch is missing, the local worktree contains unrelated dirty changes, or observed topology differs from the stack record.
8. Append a concise event naming the changed parent and rebased descendant range.

### Run A Restack Wave

A wave edits commits in the middle of a stack, sometimes reorders PRs, then force-pushes every descendant and fixes GitHub PR bases. Build it as real commits with git's rebase. A later change to the wave is then an amend and another rebase.

1. Claim the stack as the Core Rules describe, after checking for an open claim.
2. Fetch the workspace, then build the wave in a throwaway clone that borrows the workspace's objects, with local branches made from `origin/*` and rerere enabled:

   ```bash
   git -C <workspace> fetch origin
   git clone --reference <workspace> <remote-url> /tmp/wave/<repo>
   git -C /tmp/wave/<repo> config rerere.enabled true
   git -C /tmp/wave/<repo> config rerere.autoUpdate true
   for branch in <stack branches>; do git -C /tmp/wave/<repo> branch --force "$branch" "origin/$branch"; done
   ```

   `git rebase --update-refs` does not move a branch that is checked out in another worktree, and the workspace usually has several. The clone has none. It does move every other local branch that points into the rebased range, so keep backups outside `refs/heads`, for example `git update-ref refs/backup/<wave>/<branch> origin/<branch>`.
3. Make the edits as one commit on a branch of their own, on top of the stack tip. Route them with `scripts/stack-absorb` from the skill directory, dry run first:

   ```bash
   git -C /tmp/wave/<repo> switch -c wave-edits <top branch>
   # edit files in /tmp/wave/<repo>, then
   git -C /tmp/wave/<repo> commit -am "Wave edits"
   ./scripts/stack-absorb --repo /tmp/wave/<repo> --stack <stack> --edits wave-edits
   ./scripts/stack-absorb --repo /tmp/wave/<repo> --stack <stack> --edits wave-edits \
     --pin src/helper.ts=<pr> --apply --message "[{ticket}] Write one expect per fact"
   ```

   The dry run shows which PR each hunk goes to and why, and checks that the rebuilt tip equals the edited tree. A hunk that only adds lines, such as a helper moving down to an earlier PR, needs a `--pin`.
4. For what absorb cannot express, such as reordering PRs or moving a commit between PRs, run one `git rebase -i --update-refs` over the whole stack. Agents have no interactive terminal, so set `GIT_SEQUENCE_EDITOR` to a script that rewrites the todo file, and `GIT_EDITOR=true` so no message editor opens. Moving a PR means moving its `pick` lines together with its `update-ref` line. Until the stack record matches the new order, pass the order to the scripts with `--branches`.
5. A conflict stops the rebase in the normal way. Resolve it, `git add` the files, and run `GIT_EDITOR=true git rebase --continue`. rerere records the resolution. When the same conflict comes up again, rerere applies the recorded resolution. `stack-absorb` then stages it and continues by itself, and lists each file rerere resolved at the end. A plain `git rebase` still stops there: check the staged result and continue.
6. When a ruling or the order changes, reset the stack branches to `origin/*`, amend `wave-edits` or the sequence editor script, and run the absorb or rebase again. Only new conflicts need attention.
7. Run the checks on every head with `scripts/stack-verify`. Heads whose trees did not change come from its cache.
8. Review what changed per PR with `scripts/stack-range-diff`, and use `--markdown` for a note to reviewers.
9. Write the push script with `scripts/stack-push --sync-workspace <workspace> --output /tmp/wave/push.sh` and hand it to the user. Do not run it. If PRs moved, update the stack record's order, `parent` and `base_ref` to match; `stack.py` has no reorder command, so this is a direct TOML edit followed by `validate`.
10. After the user runs the script, record a `push` event, which also releases the claim. If the wave is dropped instead, record an `abandon` event. Then delete the clone and the verify worktree beside it.

### Prepare A Stack Operation

Use this for rebases, force-pushes, changing PR bases, or stack-wide sync work.

1. Locate the stack, validate it, and gather the external facts needed for the operation.
2. If the user directly requested the operation, proceed without asking for second approval when topology is clear and conflicts are straightforward.
3. Present the proposed operation before acting when the request is exploratory, broad, ambiguous, or risky.
4. Include affected PRs, branch/base sequence, expected force-pushes, and risks when proposing or reporting the operation.
5. Stop and ask if conflicts are non-trivial, branch topology does not match the stack record, or any affected branch is outside the requested stack.
6. After approved or directly requested work is complete, update durable stack state and append an event.

### Coordinate With Other Skills

- After `raise-my-pr`, use `import-pr` or `add-to-stack` for the matching PR.
- Before `implement-ticket`, read stack context if the ticket appears in a stack and surface parent/descendant constraints.
- After `review-pr-feedback`, record durable stack history only if the feedback caused a meaningful stack state change.
- After `prepare-review-request`, record durable stack history only if there is useful handoff context.
