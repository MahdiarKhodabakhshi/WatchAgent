# Repository Agent Instructions

## Two-Agent Development System

This repository uses a local, subscription-first two-agent workflow. The repo is the shared memory between the agents, and human approval is the authority for implementation.

### Roles

- Codex is the Planner / Product + Architecture + Security Analyst.
- Claude Code is the Implementer / Engineer.
- Codex may propose tasks, create plans, and review PRs.
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
- Agents must never merge pull requests.
- Implementation work must happen on a task branch named `agent/<task-id-slug>`.
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
- If another issue is discovered, record it as a proposed task instead of expanding the current implementation.
- Session recovery must resume the same task or stop with a clear handoff.

### Subscription-Only Rule

- This workflow is subscription-first and local-first.
- Codex must use the local Codex CLI with ChatGPT login.
- Claude must use Claude Code with subscription OAuth.
- Agent scripts must fail closed if API-key environment variables for Codex/OpenAI or Claude/Anthropic are detected.
- No task may add API keys, secrets, or paid key-backed automation to this system.
