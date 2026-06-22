# Pending Approval: TASK-frontend-dashboard-state-polish

- Title: Polish dashboard loading empty and error states
- Status: proposed
- Risk: medium
- Area: frontend
- Approval required: true
- Approved by: None
- Created at: 2026-06-21T22:36:50Z
- Materialized at: 2026-06-21T22:37:55Z
- Task file: .agent/tasks/TASK-frontend-dashboard-state-polish.json

## Objective

Improve the existing dashboard states so loading, empty, cold-start, and API-error cases are clear, accessible, and visually consistent without changing backend contracts or product scope.

## Context

The project brief prioritizes frontend dashboard loading, empty, and error states. This is user-visible UI work, so it is medium risk. The UI should help users understand whether the app is loading, has no data yet, is filtered to an empty result, or cannot reach the API, while avoiding claims stronger than detector evidence supports.

## Implementation Plan

- Read `frontend/src/App.tsx`, components under `frontend/src/components/`, API client code under `frontend/src/api/`, state code under `frontend/src/state/`, and styling conventions in `frontend/src/index.css` and `frontend/src/design/`.
- Identify current loading, empty, and error handling for readings/events and filters.
- Implement small UI improvements using existing component and styling patterns; avoid broad redesigns or new dependencies.
- Differentiate fresh-database or no-results states from API failure states when current data flow supports that distinction.
- Preserve existing API query parameters, response contracts, filters, and routing behavior.
- Update `.agent/handoff.md` with changed files, verification commands, and any limitations.

## Acceptance Criteria

- Dashboard has clear visible states for loading, empty results, and API errors in the main event/readings experience.
- State copy is concise, does not overclaim detector accuracy, and remains understandable to non-expert users.
- Existing filters for city, time window, event type, severity, and custom day windows continue to work as before.
- The UI remains responsive on common mobile and desktop widths, with no obvious text overlap or layout breakage.
- No backend API contract, detector logic, database schema, deployment configuration, package dependency, auth, notification, or external-service behavior is changed.

## Test Plan

- Run the frontend lint command if discoverable, likely `npm run lint` from `frontend/`.
- Run the frontend build command if discoverable, likely `npm run build` from `frontend/`.
- If practical, manually inspect the dashboard in normal, empty, and failed-API states and record what was checked.
- Record commands, results, and any unrun checks in `.agent/handoff.md`.

## Files Likely To Change

- frontend/src/App.tsx
- frontend/src/components
- frontend/src/api
- frontend/src/state
- frontend/src/index.css
- .agent/handoff.md

## Forbidden Changes

- Do not modify secrets or print secret values.
- Do not change backend API behavior, detector thresholds, scoring, lifecycle, database schema, Docker/deployment configuration, auth, billing, notifications, external services, or paid API-key flows.
- Do not add new frontend dependencies unless separately approved.
- Do not replace the dashboard with a marketing or landing page.
- Do not weaken accessibility or remove existing filters.

## Done Definition

- Dashboard state polish is implemented within existing frontend architecture and scope.
- Relevant frontend verification passes or blockers are documented.
- `.agent/handoff.md` records changed files, commands run, results, manual checks, and residual risks.

## Approval Command

```bash
scripts/agent-approve.sh TASK-frontend-dashboard-state-polish
```
