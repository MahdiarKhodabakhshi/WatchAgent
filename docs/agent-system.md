# Local Two-Agent Development System

This repository uses a local, subscription-first workflow:

- Codex plans, writes task specs, and reviews implementation branches.
- Claude Code implements exactly one approved task at a time.
- The repository is shared memory through `.agent/`.
- The human approves work before implementation.
- Agents never push to `main` or `master` and never merge pull requests.

## Shared Files

- `.agent/project_brief.md`: Human-owned product context.
- `.agent/operating_rules.md`: Contract both agents must follow.
- `.agent/backlog.md`: Proposed and approved work queues.
- `.agent/tasks/`: JSON task specs.
- `.agent/plans/`: Codex planning notes.
- `.agent/handoff.md`: Resumable current-state handoff.
- `.agent/reviews/`: Codex review outputs.
- `.agent/approvals/`: Human approval records.
- `.agent/logs/`: Local script logs, ignored by git.

## Fill In The Project Brief

Before planning product work, edit `.agent/project_brief.md` and fill in:

- Project name and one-sentence goal.
- Target users.
- Current MVP features.
- Planned features.
- Competitors or alternatives.
- Design and product principles.
- Non-goals.
- Risky areas.
- Approval policy.

Keep risky areas explicit. Security, auth, migrations, deployment, billing, destructive commands, and external services need human approval before implementation.

## Check The Environment

Run:

```bash
scripts/agent-env-check.sh
```

The check verifies required CLIs, confirms the repo is a git repo, prints the current branch, warns on dirty worktrees, and fails closed if known API-key auth variables are present. Use ChatGPT login for Codex and subscription OAuth for Claude Code.

## Run The Planner

Create or switch to a non-main branch, then run:

```bash
scripts/codex-planner.sh
```

Codex reads the project brief, operating rules, backlog, repo structure, recent history, and available GitHub context. It may write plans and task JSON files under `.agent/`, but it must not implement application code.

New tasks are `proposed` by default. Tasks listed under `Approved Now` in `.agent/backlog.md` may be emitted as `approved`.

## Approve A Task

Review the proposed task JSON in `.agent/tasks/`, then approve one task:

```bash
scripts/agent-approve.sh TASK-ID
```

High-risk tasks require an explicit flag:

```bash
scripts/agent-approve.sh --allow-high-risk TASK-ID
```

Approval changes the task status to `approved`, sets `approved_by` to `human`, and writes an approval record.

## Run The Implementer

From a non-main branch, run:

```bash
scripts/claude-implementer.sh
```

The script finds the oldest approved task, creates or switches to `agent/<task-id-slug>`, asks Claude Code to implement exactly that task, asks it to update `.agent/handoff.md`, commits changes if possible, pushes if a remote exists, and creates a draft PR when `gh` is authenticated.

The script does not merge.

## Run The Reviewer

After implementation, run:

```bash
scripts/codex-reviewer.sh TASK-ID
```

Codex reviews in read-only mode and the wrapper writes the review to `.agent/reviews/`. The review checks scope, tests, docs, security, architecture, and acceptance criteria.

## Optional One-Cycle Loop

Run one conservative cycle:

```bash
scripts/agent-loop.sh
```

The default is one cycle only. To keep cycling locally:

```bash
scripts/agent-loop.sh --forever
```

Stop with `Ctrl+C`. The loop does not hide failures.

## Avoid Paid API Usage

- Use subscription login for Codex and Claude Code.
- Do not add API keys to files.
- Do not create or modify secrets.
- Do not bypass `scripts/agent-env-check.sh`.
- Do not change the scripts to allow key-backed execution.

## Recover After Session Limits

If Claude Code hits a session or usage limit, the implementer script stops without retry spam. It preserves local changes, commits a WIP checkpoint when possible, and appends to `.agent/handoff.md`.

To resume:

1. Inspect `.agent/handoff.md`.
2. Confirm you are on the same `agent/<task-id-slug>` branch.
3. Re-run `scripts/claude-implementer.sh` after access is available again.

## Never Allow

- Direct pushes to `main` or `master`.
- Agent-merged PRs.
- Unapproved implementation work.
- Secrets in files, logs, commits, task specs, or prompts.
- Unapproved security, auth, database migration, deployment, payment, billing, destructive-command, or external-service changes.
- Sandbox or permission bypasses.
