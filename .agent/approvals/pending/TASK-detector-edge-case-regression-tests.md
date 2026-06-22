# Pending Approval: TASK-detector-edge-case-regression-tests

- Title: Add detector edge-case regression tests without threshold changes
- Status: proposed
- Risk: low
- Area: backend-tests
- Approval required: true
- Approved by: None
- Created at: 2026-06-21T22:36:50Z
- Materialized at: 2026-06-21T22:37:55Z
- Task file: .agent/tasks/TASK-detector-edge-case-regression-tests.json

## Objective

Add focused regression tests for existing detector edge cases while preserving current thresholds, scoring, lifecycle behavior, and evaluation claims.

## Context

Detector logic is a risky area, but the backlog explicitly calls for identifying small low-risk tests. This task is limited to tests that document existing behavior. It must not tune thresholds, alter scoring, change severity mapping, or modify lifecycle behavior.

## Implementation Plan

- Review existing detector tests, especially `tests/test_native_detectors.py`, `tests/test_detection_contract.py`, `tests/test_lifecycle.py`, and detector modules under `app/detection/`.
- Select one or two narrowly scoped edge cases with clear current behavior, such as missing optional weather fields, borderline non-trigger cases, lifecycle hysteresis behavior, or confidence suppression with incomplete context.
- Add tests using existing fixtures and helper patterns, keeping scenarios deterministic and network-free.
- Do not change detector implementation unless a test exposes a clear bug already covered by an approved separate task; otherwise record a follow-up proposed task in `.agent/handoff.md`.
- Run the smallest relevant detector test subset and document results.

## Acceptance Criteria

- New tests cover existing detector edge-case behavior without changing thresholds, scoring weights, severity mapping, lifecycle state transitions, or stored event contracts.
- Tests are deterministic and do not depend on live Open-Meteo responses.
- The added coverage uses existing test patterns and keeps fixtures minimal.
- Any unexpected detector behavior is documented as a follow-up instead of being fixed within this task.
- No evaluation metrics or README performance claims are changed.

## Test Plan

- Run the touched detector test file directly, such as `pytest tests/test_native_detectors.py` or the specific file modified.
- Run adjacent detector contract/lifecycle tests if touched behavior crosses boundaries, such as `pytest tests/test_detection_contract.py tests/test_lifecycle.py`.
- Record commands and results in `.agent/handoff.md`.

## Files Likely To Change

- tests/test_native_detectors.py
- tests/test_detection_contract.py
- tests/test_lifecycle.py
- .agent/handoff.md

## Forbidden Changes

- Do not modify detector thresholds, scoring weights, severity cutoffs, event lifecycle behavior, climatology artifacts, evaluation metrics, or README claims.
- Do not modify secrets or print secret values.
- Do not add live network calls, external services, paid API-key flows, auth, billing, notification integrations, deployment changes, or database migrations.
- Do not delete or weaken existing tests.

## Done Definition

- Focused detector regression tests are added without behavior changes.
- Relevant detector tests pass or failures are documented with residual risk.
- `.agent/handoff.md` records changed files, verification results, blockers, and any proposed follow-up.

## Approval Command

```bash
scripts/agent-approve.sh TASK-detector-edge-case-regression-tests
```
