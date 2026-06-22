# Repository Agent Instructions

## Two-Agent Development System

This repository uses a local, subscription-first two-agent workflow. The repo is the shared memory between the agents, and human approval is the authority for implementation.

### Roles

- Codex is the Planner / Product + Architecture + Security Analyst.
- Claude Code is the Implementer / Engineer.
- Codex may propose tasks, create plans, and review work branch diffs or PRs.
- Codex must not implement application code unless a human explicitly asks for implementation in the current session.
- Claude may only implement tasks that are already approved in `.agent/tasks/`.

### Approval Rules

- New product features require human approval before implementation.
- Architecture changes require human approval before implementation.
- Security-sensitive changes, auth changes, database migrations, deployment changes, payment or billing changes, destructive commands, and new external services require human approval before implementation.
- Approved work must be represented as a task JSON file with status `approved`.
- Proposed work must remain status `proposed` until a human approves it.
- High-risk work requires explicit human approval and must not be bundled with low-risk work.

### Branch Rules

- Agents must never push directly to `main` or `master`.
- Agents must never work on `main` or `master`.
- Agents must never push to branches listed in `AGENT_PROTECTED_BRANCHES`.
- Agents must never merge pull requests.
- The recommended portable branch mode is `single_work_branch`.
- In `single_work_branch` mode, implementation work must happen on `AGENT_WORK_BRANCH` (default: `agent_developed`).
- In `single_work_branch` mode, agents must not create per-task branches by default.
- Agents may commit to `AGENT_WORK_BRANCH`.
- Human review is required before any final merge or cherry-pick to `main` or `master`.
- One implementation run should handle exactly one approved task.
- Pull requests created by agents must be draft PRs unless a human says otherwise.

### Testing Rules

- Claude must run relevant lint, test, typecheck, or build commands when they are discoverable.
- If tests cannot be run, the reason must be recorded in `.agent/handoff.md`.
- Codex reviews must check acceptance criteria, tests, docs, security, architecture, and scope.

### Documentation Rules

- Behavior changes and setup changes require README or docs updates when relevant.
- Agent workflow changes must be documented in `.agent/` files or `docs/agent-system.md`.
- Do not hide operational decisions in chat only; keep durable state in the repo.

### Forbidden Areas

- Agents must not create, modify, or commit secrets.
- Agents must not use paid API-key flows for this workflow.
- Agents must not bypass sandboxing, approval gates, branch protections, or review gates.
- Agents must not merge PRs, force-push shared branches, rewrite protected history, or run destructive commands without human approval.
- Agents must not add new external services, auth flows, billing/payment flows, deployment changes, or database migrations without human approval.

### One-Task-Per-Run Rule

- Claude implements one approved task per run.
- The autonomous loop implements at most one actionable task per implementation cycle.
- If another issue is discovered, record it as a proposed task instead of expanding the current implementation.
- Session recovery must resume the same task or stop with a clear handoff.

### Loop Rules

- Proposed tasks require human approval before implementation.
- Tasks with status `needs_revision` may be fixed without reapproval only when the fix stays within the original approved task scope.
- A task marked `implemented` still requires human final review and merge/cherry-pick.
- Agents must not auto-merge PRs.
- Human operators are responsible for final review and merge/cherry-pick from `AGENT_WORK_BRANCH` to protected branches.
- Codex review verdicts drive final loop state: `accepted`, `needs_revision`, or `blocked`.

### Subscription-Only Rule

- This workflow is subscription-first and local-first.
- Codex must use the local Codex CLI with ChatGPT login.
- Claude must use Claude Code with subscription OAuth.
- Agent scripts must fail closed if API-key environment variables for Codex/OpenAI or Claude/Anthropic are detected.
- No task may add API keys, secrets, or paid key-backed automation to this system.
