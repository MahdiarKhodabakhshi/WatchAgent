# Local Two-Agent Development System

This repository uses a local, subscription-first autonomous loop:

1. The wrapper gathers repository context.
2. Codex emits delta planner JSON only.
3. The wrapper validates and materializes plans, new tasks, and missing approvals.
4. If no actionable approved task exists, the wrapper notifies the human and stops, unless `--forever` is active.
5. Claude Code implements exactly one actionable task.
6. Claude or the wrapper opens or updates a draft PR when possible.
7. Codex reviews the branch and writes structured review artifacts.
8. The loop updates task state from the review verdict.
9. In `--forever` mode, the loop sleeps and repeats.

Agents never push directly to `main` or `master`, never merge PRs, and never use API-key backed automation for this workflow.

## Shared Files

- `.agent/project_brief.md`: Human-owned product context.
- `.agent/operating_rules.md`: Contract both agents must follow.
- `.agent/backlog.md`: Proposed and approved work queues.
- `.agent/tasks/`: JSON task specs and loop-managed task state.
- `.agent/plans/`: Codex planner result notes.
- `.agent/handoff.md`: Resumable current-state handoff.
- `.agent/reviews/`: Markdown and JSON Codex review outputs.
- `.agent/approvals/`: Human approval records.
- `.agent/notifications/`: Local notification markdown files, ignored by git.
- `.agent/logs/`: Local script logs, ignored by git.
- `.agent/tmp/`: Local planner/reviewer prompts and transient output, ignored by git.

## Environment Check

Run:

```bash
scripts/agent-env-check.sh
```

The check verifies required CLIs, confirms the repo is a git repo, prints the current branch, warns on dirty worktrees, and fails closed if known Codex/OpenAI or Claude/Anthropic API-key variables are set. Use ChatGPT login for Codex and subscription OAuth for Claude Code.

## One-Cycle Mode

From a non-main branch:

```bash
scripts/agent-loop.sh
```

The default runs one complete cycle: plan, select one actionable task, implement, open or update a draft PR, review, update task state, checkpoint, then stop. It refuses to run on `main` or `master`.

If planning fails but an existing `approved`, `in_progress`, or `needs_revision` task is already available, the loop records the planner failure and continues with that one actionable task. If planning fails and no actionable task exists, the loop writes a `loop_failed` notification and stops.

## Forever Mode

Run:

```bash
scripts/agent-loop.sh --forever
```

Useful options:

```bash
scripts/agent-loop.sh --forever --sleep-seconds 1800
scripts/agent-loop.sh --forever --max-iterations 3
```

The loop sleeps between cycles and stops after more than 3 consecutive planner/reviewer/checkpoint failures. `Ctrl+C` stops safely. Claude auth, usage, or max-turn stops terminate immediately after checkpointing.

## Approval Gate

Planner output creates proposed tasks and pending approval files. Review `.agent/approvals/pending/`, then approve exactly one task:

```bash
scripts/agent-approve.sh TASK-ID
```

High-risk tasks require explicit approval:

```bash
scripts/agent-approve.sh --allow-high-risk TASK-ID
```

If no actionable task exists, the loop writes a notification and prints:

```text
No approved tasks. Review .agent/approvals/pending/ and approve one with scripts/agent-approve.sh TASK-ID.
```

## Task Status Lifecycle

Allowed task statuses:

- `proposed`: Planned but not approved.
- `approved`: Human approved and eligible for implementation.
- `in_progress`: Selected by the implementer.
- `needs_revision`: Codex review found required fixes inside the original approved scope.
- `implemented`: Codex review accepted the implementation; human PR review and merge are still required.
- `blocked`: Human decision or external access is needed.
- `rejected`: Human rejected the proposed task.

Implementation priority is deterministic: `needs_revision`, then `in_progress`, then `approved`. `proposed`, `rejected`, `implemented`, and `blocked` are ignored for implementation.

Use the state helper for manual inspection or repair:

```bash
scripts/agent-task-state.py list-actionable
scripts/agent-task-state.py get-next
scripts/agent-task-state.py mark-blocked TASK-ID --reason "human decision needed"
```

## Planner Behavior

Run the planner directly when needed:

```bash
scripts/codex-planner.sh
```

The wrapper gathers context into `.agent/tmp/planner-context.md`, asks Codex for JSON only, validates the output, and materializes files through `scripts/materialize-planner-output.py`. Existing task files are not overwritten.

Planner output is delta-based. Codex should emit full task objects only for new tasks, while `recommended_order` may reference task IDs that already exist in `.agent/tasks/`. If no new tasks are needed, the planner returns `"tasks": []` and keeps any still-relevant existing task IDs in `recommended_order`.

The materializer validates `recommended_order` against both task IDs in the current planner output and existing `.agent/tasks/*.json` files, ignoring `TASK-TEMPLATE.json`. Existing task files are not overwritten. For existing `proposed` tasks, the materializer only creates a missing pending approval file; proposed tasks still require human approval before implementation. Existing `approved`, `in_progress`, `needs_revision`, `implemented`, and `blocked` tasks are not regenerated.

## Implementer Behavior

Run the implementer directly for the next actionable task:

```bash
scripts/claude-implementer.sh
```

Or pass an explicit task id:

```bash
scripts/claude-implementer.sh TASK-ID
```

The script marks the task `in_progress`, switches to `agent/<task-id-slug>`, runs Claude Code with subscription auth, asks Claude to update `.agent/handoff.md`, commits implementation changes when possible, pushes the task branch, and opens or updates a draft PR when `gh` can do so.

Claude must not mark the task implemented. The review verdict controls final status.

## Review Behavior

Run the reviewer after implementation:

```bash
scripts/codex-reviewer.sh TASK-ID
```

The reviewer runs Codex in read-only mode and writes:

- `.agent/reviews/REVIEW-<TASK-ID>-<timestamp>.json`
- `.agent/reviews/REVIEW-<TASK-ID>-<timestamp>.md`

The JSON verdict is one of:

- `accepted`: loop marks the task `implemented`, writes a completion note, and notifies the human that the PR is ready.
- `needs_revision`: loop marks the same task `needs_revision`; the next cycle sends it back to Claude without new approval if fixes stay in scope.
- `blocked`: loop marks the task `blocked` and notifies the human.

Accepted does not mean merged. Humans still review and merge PRs.

## Notifications

Run manually when needed:

```bash
scripts/agent-notify.sh approval_needed
scripts/agent-notify.sh review_ready TASK-ID
```

Supported reasons are `approval_needed`, `implementation_blocked`, `review_ready`, `auth_failed`, `usage_limit`, and `loop_failed`. Each notification includes the reason, timestamp, branch, current actionable task, pending approvals, and the next command for the human.

GitHub issue creation is best-effort. If `gh` is authenticated and a remote exists, approval and review notifications can create or comment on issues labeled `agent/approval-needed` or `agent/review-needed`. GitHub is not required for success.

## PR Behavior

Implementation branches are named `agent/<task-id-slug>`. PRs created by agents are draft PRs unless a human says otherwise. Agents never merge PRs and never push directly to `main` or `master`.

If `gh` cannot create a PR, the implementer records that in `.agent/handoff.md` and continues after preserving local changes.

## Recovery

For Claude auth errors:

1. Inspect `.agent/handoff.md` and the latest `.agent/logs/claude-implementer-*.log`.
2. Re-authenticate Claude Code with subscription OAuth.
3. Confirm API-key environment variables are unset.
4. Re-run `scripts/agent-loop.sh` or `scripts/claude-implementer.sh TASK-ID`.

For usage or max-turn stops:

1. Inspect `.agent/handoff.md`.
2. Wait for usage to reset, or increase `CLAUDE_MAX_TURNS` for a scoped retry.
3. Resume the same task with `scripts/agent-loop.sh`.

For planner failures, inspect `.agent/logs/agent-loop-events.log` and the latest Codex planner log. Already-actionable approved, in-progress, or revision work can still continue through the loop. For reviewer or checkpoint failures, inspect `.agent/logs/agent-loop-events.log` and rerun one cycle after fixing the cause.

## Avoid Paid API Usage

- Use local Codex CLI with ChatGPT login.
- Use Claude Code with subscription OAuth.
- Do not set `OPENAI_API_KEY`, `CODEX_API_KEY`, `ANTHROPIC_API_KEY`, or `ANTHROPIC_AUTH_TOKEN`.
- Do not add secrets or API keys to files, prompts, logs, tasks, or commits.
- Do not change scripts to allow paid key-backed automation.
