# Pending Approval: TASK-api-query-validation-regressions

- Title: Add focused API query validation regression tests
- Status: proposed
- Risk: low
- Area: backend-tests
- Approval required: true
- Approved by: None
- Created at: 2026-06-21T22:36:50Z
- Materialized at: 2026-06-21T22:37:55Z
- Task file: .agent/tasks/TASK-api-query-validation-regressions.json

## Objective

Strengthen confidence in existing `/readings` and `/events` query validation behavior with focused tests that document current API contracts without changing behavior.

## Context

The backlog calls for small low-risk tests and API error consistency review. This task is test-focused: it should capture current behavior around valid and invalid query parameters, limits, city filters, and datetime filters. If current behavior appears inconsistent or undesirable, record a follow-up proposed task instead of changing API behavior in this task.

## Implementation Plan

- Read `tests/test_api.py`, `app/main.py`, `app/schemas.py`, and relevant storage/query code to understand current API behavior.
- Add narrow tests for existing `/readings` and `/events` validation behavior, prioritizing invalid city values, invalid datetime inputs, limit bounds, and combinations of supported filters.
- Use existing test fixtures and local database setup patterns; do not introduce live Open-Meteo calls.
- Keep assertions aligned with the existing FastAPI response shape and current status codes.
- If a behavior gap is discovered that requires implementation changes, document it in `.agent/handoff.md` as a proposed follow-up rather than expanding this task.

## Acceptance Criteria

- New tests document current API query validation and filtering behavior for `/readings` and `/events`.
- Tests do not depend on live network calls or external services.
- No API implementation behavior is changed unless an existing test fixture requires a harmless setup adjustment within scope.
- The test additions are small and readable, using existing project test conventions.
- Any discovered behavior improvement is recorded as a follow-up rather than implemented in this task.

## Test Plan

- Run `pytest tests/test_api.py`.
- If broader impact is plausible, run the backend test subset related to storage and schemas, such as `pytest tests/test_api.py tests/test_dedup.py tests/test_forecast_storage.py`.
- Record commands, results, and any failures in `.agent/handoff.md`.

## Files Likely To Change

- tests/test_api.py
- .agent/handoff.md

## Forbidden Changes

- Do not modify secrets or print secret values.
- Do not change production API behavior, response contracts, database schema, detector logic, lifecycle behavior, deployment files, or package files.
- Do not add live network dependencies to tests.
- Do not delete or weaken existing tests to make the suite pass.
- Do not introduce auth, billing, notification integrations, external services, paid API-key flows, or database migrations.

## Done Definition

- Focused API validation regression tests are added and scoped.
- Relevant pytest commands pass or failures are documented with residual risk.
- `.agent/handoff.md` records changed files, verification results, blockers, and any follow-up task ideas.

## Approval Command

```bash
scripts/agent-approve.sh TASK-api-query-validation-regressions
```
