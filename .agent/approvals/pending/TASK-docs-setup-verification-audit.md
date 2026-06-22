# Pending Approval: TASK-docs-setup-verification-audit

- Title: Audit local setup and verification documentation
- Status: proposed
- Risk: low
- Area: docs
- Approval required: true
- Approved by: None
- Created at: 2026-06-21T22:36:50Z
- Materialized at: 2026-06-21T22:37:55Z
- Task file: .agent/tasks/TASK-docs-setup-verification-audit.json

## Objective

Make the local setup and verification documentation accurate, easy to follow, and consistent with the current Docker, backend, frontend, evaluation, and agent workflow commands.

## Context

The backlog calls for README and developer documentation improvements, setup-gap documentation, troubleshooting notes, and fastest local verification commands. This should be a documentation-only task and must not change application behavior, dependencies, deployment configuration, or agent approval rules.

## Implementation Plan

- Review `README.md`, `docs/agent-system.md`, `.agent/project_brief.md`, `.agent/operating_rules.md`, and discoverable project commands in `pyproject.toml`, frontend package files, Docker files, and scripts.
- Identify stale, missing, or ambiguous setup instructions, including local database recreation, frontend/backend verification, evaluation scripts, and common failure modes.
- Update the smallest relevant docs to clarify commands and troubleshooting without adding new workflow requirements.
- Keep claims about detector quality and evaluation aligned with existing evidence; do not add unverified performance claims.
- Update `.agent/handoff.md` with the task status, changed files, verification run, and any commands that could not be run.

## Acceptance Criteria

- README or docs clearly state the normal local setup path and the fastest useful verification commands for backend, frontend, and Docker usage where applicable.
- Troubleshooting guidance covers common local issues already implied by the repo, such as port conflicts, fresh database state, and empty readings/events before polling or backfill.
- Documentation remains consistent with the subscription-first two-agent workflow and does not introduce API-key-backed automation.
- No application source, frontend source, Docker/deployment behavior, package metadata, detector logic, or database schema is changed.
- Any commands documented as verification commands are either run successfully or explicitly recorded in `.agent/handoff.md` with the reason they could not run.

## Test Plan

- Run the smallest relevant documentation sanity checks available, such as rendering-neutral grep/review checks and any existing docs-related lint if discoverable.
- Run or dry-check documented commands only when they are safe and local; do not start destructive cleanup or long-running backfills unless already documented as optional.
- Record all commands and outcomes in `.agent/handoff.md`.

## Files Likely To Change

- README.md
- docs/agent-system.md
- .agent/handoff.md

## Forbidden Changes

- Do not modify secrets or print secret values.
- Do not modify application source, frontend source, Docker/deployment files, package files, database files, detector thresholds, scoring, lifecycle, or API behavior.
- Do not add paid API-key flows, auth, billing, notification integrations, database migrations, external services, or destructive commands.
- Do not change agent approval gates, branch rules, or one-task-per-run rules.

## Done Definition

- Documentation changes are complete and scoped to setup, verification, and troubleshooting clarity.
- Relevant safe verification has passed or blockers are documented.
- `.agent/handoff.md` records changed files, commands run, results, residual risks, and next steps.

## Approval Command

```bash
scripts/agent-approve.sh TASK-docs-setup-verification-audit
```
