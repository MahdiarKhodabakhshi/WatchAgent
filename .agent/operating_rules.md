# Operating Rules

This is the shared contract for Codex, Claude, and the human operator.

## Codex Planner Responsibilities

- Read `.agent/project_brief.md`, `.agent/operating_rules.md`, `.agent/backlog.md`, and relevant repository context before planning.
- Propose scoped tasks as JSON files in `.agent/tasks/`.
- Create or update plans in `.agent/plans/`.
- Keep proposed tasks status `proposed` unless they are explicitly listed under `Approved Now` in `.agent/backlog.md`.
- Review implementation branches and draft PRs against task specs.
- Check scope, security, architecture, docs, tests, and acceptance criteria.
- Do not implement application code unless the human explicitly asks Codex to implement in the current session.

## Claude Implementer Responsibilities

- Implement exactly one approved task per run.
- Read the selected task JSON, this file, `.agent/handoff.md`, and relevant project files before editing.
- Stay within the objective, acceptance criteria, and forbidden changes in the task.
- Run discoverable relevant lint, test, typecheck, or build commands.
- Update `.agent/handoff.md` before stopping.
- Update README or docs only when behavior or setup changed.
- Do not invent or implement unapproved product, architecture, security, auth, database, deployment, billing, destructive-command, or external-service changes.

## Allowed Task Statuses

- `proposed`: Codex or a human suggested the work; Claude must not implement it.
- `approved`: A human approved the work; Claude may implement it.
- `rejected`: A human rejected the work.
- `in_progress`: An agent is actively working on it.
- `implemented`: Claude believes implementation is complete and ready for review.
- `needs_revision`: Review found required changes.
- `blocked`: Work cannot continue without human input or an external condition.

## Approval Gate

- Human approval is required before Claude implements any task.
- New product features, architecture changes, security-sensitive changes, auth changes, database migrations, deployment changes, payment or billing changes, destructive commands, and new external services require explicit approval.
- High-risk tasks require explicit human approval and should be split where possible.
- If task scope is ambiguous, stop and record the blocker instead of implementing.

## Risk Levels

- `low`: Localized maintenance, docs, tests, small UI fixes, or internal cleanup with no behavior risk.
- `medium`: User-visible behavior changes, nontrivial refactors, integration changes, or changes with moderate regression risk.
- `high`: Security, auth, data migrations, payments, deployment, destructive operations, new external services, or broad architecture changes.

## Forbidden Changes

- Do not create, modify, commit, print, or request secrets.
- Do not use paid API-key flows for this workflow.
- Do not bypass sandboxing, permission prompts, branch rules, or review gates.
- Do not push to `main` or `master`.
- Do not merge pull requests.
- Do not run destructive commands without explicit human approval.
- Do not add external services, auth flows, payment or billing behavior, deployment changes, or database migrations without explicit human approval.

## Git Rules

- Work from a non-main branch.
- Claude branches must be named `agent/<task-id-slug>`.
- Commit only task-related changes.
- Prefer draft PRs for agent-created PRs.
- Never merge PRs from an agent script.
- Preserve user changes. If unrelated dirty work exists, do not overwrite it.

## Test Rules

- Run the smallest relevant verification first.
- Broaden verification when touching shared behavior, app contracts, database behavior, security-sensitive code, or user-facing flows.
- Record commands and results in `.agent/handoff.md`.
- If a command cannot run, record the reason and residual risk.

## Docs Rules

- Update docs when setup, commands, user-facing behavior, or operational expectations change.
- Keep agent-state docs in `.agent/` and human guidance in `docs/`.
- Do not bury required state only in chat logs.

## Handoff Rules

- Update `.agent/handoff.md` before stopping.
- Include current task, branch, status, changed files, tests run, blockers, next steps, and do-not-touch items.
- If work is incomplete, make the next action explicit.

## Session-Limit Recovery Rules

- If an agent hits a session, usage, or auth limit, stop retrying.
- Preserve all local changes.
- Commit a WIP checkpoint when possible and safe.
- Update `.agent/handoff.md` with the exact stopping point.
- Resume in a later session from the same task and branch.
