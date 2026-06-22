# Pending Approval: TASK-agent-workflow-review-schema-validation

- Title: Validate reviewer output schema integration
- Status: proposed
- Risk: low
- Area: agent-workflow
- Approval required: true
- Approved by: None
- Created at: 2026-06-22T00:10:51Z
- Materialized at: 2026-06-22T00:12:42Z
- Task file: .agent/tasks/TASK-agent-workflow-review-schema-validation.json

## Objective

Make `.agent/schemas/review.schema.json` intentional durable workflow state by validating and documenting how reviewer output should use it, or record a blocker if its intended role is unclear.

## Context

Git status shows an untracked `.agent/schemas/review.schema.json` on the active agent branch. The repository treats `.agent/` as shared durable state for the two-agent workflow, so schema files should not remain ambiguous. This task is limited to local agent workflow metadata, reviewer validation, and documentation; it must not change application behavior.

## Implementation Plan

- Read `.agent/schemas/review.schema.json`, `.agent/schemas/planner-output.schema.json`, `.agent/schemas/task.schema.json`, `scripts/codex-reviewer.sh`, `scripts/materialize-planner-output.py`, `docs/agent-system.md`, and `.agent/operating_rules.md`.
- Validate that the review schema is syntactically valid JSON Schema and matches the intended reviewer output shape.
- If the schema is intended, make the smallest workflow-scoped update needed so reviewer tooling or documentation clearly references it.
- If the schema is not intended or its ownership is unclear, do not delete it; record the ambiguity and required human decision in `.agent/handoff.md`.
- Keep subscription-first auth, approval gates, branch rules, sandbox rules, and one-task-per-run behavior unchanged.

## Acceptance Criteria

- The role of `.agent/schemas/review.schema.json` is no longer ambiguous: it is either integrated and documented, or explicitly blocked pending human direction.
- Any retained review schema is valid JSON and is referenced by relevant reviewer workflow documentation or validation tooling.
- No application source, frontend source, backend behavior, detector logic, database schema, Docker/deployment configuration, package metadata, or secrets are changed.
- Agent approval gates, branch rules, sandbox expectations, review gates, and subscription-only rules remain intact.
- `.agent/handoff.md` records changed files, verification commands, results, blockers, and residual risk.

## Test Plan

- Run JSON syntax validation for touched schema files, such as `python3 -m json.tool .agent/schemas/review.schema.json`.
- If shell scripts are touched, run `bash -n` on the touched scripts.
- If Python workflow scripts are touched, run targeted syntax checks such as `python3 -m py_compile` on the touched scripts.
- Record all commands and outcomes in `.agent/handoff.md`.

## Files Likely To Change

- .agent/schemas/review.schema.json
- scripts/codex-reviewer.sh
- scripts/materialize-planner-output.py
- docs/agent-system.md
- .agent/operating_rules.md
- .agent/handoff.md

## Forbidden Changes

- Do not modify secrets or print secret values.
- Do not modify application source, frontend source, backend behavior, detector thresholds, scoring, lifecycle behavior, database schema, Docker/deployment files, package metadata, or runtime configuration.
- Do not weaken or bypass approval gates, branch rules, sandbox rules, review gates, or one-task-per-run behavior.
- Do not add paid API-key flows, auth, billing, notification integrations, external services, destructive commands, or database migrations.
- Do not delete `.agent/schemas/review.schema.json` unless the human explicitly approves that cleanup.

## Done Definition

- The review schema is intentionally integrated or the blocker is documented for human decision.
- Relevant validation passes or limitations are recorded.
- `.agent/handoff.md` is updated with status, changed files, commands run, results, and next steps.

## Approval Command

```bash
scripts/agent-approve.sh TASK-agent-workflow-review-schema-validation
```
