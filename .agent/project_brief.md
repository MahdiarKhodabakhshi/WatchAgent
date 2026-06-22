# Project Brief — WatchAgent

## Project name

WatchAgent

## One-sentence goal

WatchAgent monitors weather data for selected Canadian cities, detects meaningful and explainable weather events, and presents those events through a reliable API and dashboard.

## Target users

Primary users:

* Developers evaluating an autonomous weather-monitoring service.
* Weather-aware operations teams who want a lightweight alert/event stream.
* Technical reviewers who care about explainable event detection.
* Future users who may want city-level weather summaries, incident timelines, and event reasoning.

Secondary users:

* Data analysts who want to inspect stored weather readings and detected incidents.
* Maintainers who want a clean, testable FastAPI + React example project.
* Developers experimenting with agent-assisted software development workflows.

## Current MVP features

The current project appears to include:

* A Python 3.11+ backend service.
* FastAPI HTTP API.
* Open-Meteo polling for Ottawa, Toronto, and Vancouver.
* SQLite persistence using SQLAlchemy.
* Deduplication of readings using city and observation timestamp.
* Weather event detection from raw readings.
* Explainable event records with numeric signal values and human-readable reasons.
* Health, readings, and events API endpoints.
* Docker/Docker Compose setup.
* React dashboard served from the same FastAPI origin.
* Filters for city, time window, event type, and severity.
* Backfill / replay tooling for historical evaluation.
* Test coverage around polling, parsing, storage, API shape, and detectors.
* Evaluation documentation for detector behavior.

## Planned features

High-priority planned improvements:

1. Improve the frontend dashboard experience.

   * Better loading states.
   * Better empty states.
   * Better error states.
   * Clearer event cards/tables.
   * More useful event detail views.
   * Better visual hierarchy for severity and event type.
   * Better mobile responsiveness.

2. Improve event explainability.

   * Make every event reason easy to understand.
   * Show the numeric signal values behind each event.
   * Provide plain-language explanations for non-expert users.
   * Add links/tooltips explaining detector types.

3. Improve backend reliability.

   * Stronger API validation.
   * More consistent error responses.
   * Better logging around polling cycles and upstream failures.
   * Better separation between polling, storage, detection, and API layers.

4. Improve testing.

   * Add or strengthen tests for detector edge cases.
   * Add frontend component tests where useful.
   * Add API contract tests.
   * Ensure upstream HTTP calls are mocked in tests.
   * Avoid tests that depend on live Open-Meteo responses.

5. Improve docs.

   * Keep README setup instructions accurate.
   * Add architecture notes.
   * Add troubleshooting notes.
   * Explain detector behavior and limitations clearly.
   * Explain how to run backfill/evaluation scripts safely.

6. Improve product value.

   * Add clearer incident timeline views.
   * Add summary cards for latest city status.
   * Add event severity trends.
   * Add city comparison views.
   * Add export/share options for events.
   * Add notification integrations only after explicit approval.

## Competitors / alternatives

Relevant alternatives and adjacent tools:

* Open-Meteo dashboard/API usage directly.
* Weather.com / commercial weather dashboards.
* Environment and Climate Change Canada weather pages.
* Weather alert notification apps.
* Grafana dashboards over weather/time-series data.
* Prometheus-style monitoring systems adapted to weather data.
* Custom scripts that poll weather APIs and send alerts.

WatchAgent should differentiate itself by being:

* Lightweight.
* Self-hostable.
* Explainable.
* Testable.
* Transparent about detector logic.
* Useful as both a software project and a weather-event analysis tool.

## Product principles

1. Explainability over magic

Every event should explain why it fired. If a detector produces a warning, the API and UI should expose the relevant signal values, thresholds, and reason.

2. Trust over volume

The system should avoid noisy event spam. It is better to detect fewer meaningful events than to overwhelm users with weak alerts.

3. Calibration matters

Weather behavior differs by city. Avoid one-size-fits-all thresholds when city-specific baselines or historical context are available.

4. No hidden live dependencies in tests

Tests should not depend on live network calls. Upstream weather API behavior should be mocked or replayed.

5. Safe local-first operation

The app should be easy to run locally with Docker Compose and should not require secrets for normal operation.

6. Preserve a simple architecture

Do not add Redis, Celery, Postgres, Kubernetes, external queues, or cloud services unless there is a clear approved reason.

7. Dashboard should make events understandable

The frontend should not merely show raw JSON. It should help users quickly understand what happened, where, when, how severe it was, and why the system detected it.

8. Evaluation should be honest

Detector evaluation should clearly separate labeled tests, replay characterization, weak-label comparisons, and real-world limitations.

## Non-goals

WatchAgent should not become:

* A full commercial weather platform.
* A replacement for official government weather alerts.
* A high-availability production alerting system without additional design work.
* A general-purpose time-series database.
* A heavy cloud-native microservice platform.
* A system that requires paid LLM/API usage to run.
* A project that silently sends notifications to users without explicit approval.
* A project that changes detector thresholds just to make metrics look better.

## Risky areas

These areas require extra caution:

1. Event detection logic

Changes to detector thresholds, baselines, severity scoring, deduplication, or lifecycle behavior can seriously affect trust. Any change must include tests and an explanation.

2. Historical evaluation

Evaluation results must be honest. Do not overclaim accuracy. Clearly distinguish synthetic/labeled tests from real-world validation.

3. Upstream weather API behavior

Open-Meteo responses may change, fail, or return incomplete data. The poller should remain resilient.

4. Database schema

Database migrations or schema changes require human approval unless the task is explicitly approved.

5. Docker/deployment configuration

Deployment-related changes require approval.

6. Notification features

Email, Telegram, WhatsApp, SMS, push notifications, or webhooks require approval before implementation.

7. Security and secrets

Do not add secrets to the repository. Do not require Anthropic/OpenAI/API keys for normal service operation.

8. Frontend claims

The UI should not make stronger claims than the detector can support.

## Approval policy

Codex Planner may do the following without approval:

* Analyze the repository.
* Review architecture.
* Review security risks.
* Review frontend/backend quality.
* Propose tasks.
* Write task specifications.
* Write plans in `.agent/plans/`.
* Write proposed task JSON files in `.agent/tasks/`.
* Review Claude's implementation PRs.
* Suggest README/docs improvements.

Codex Planner must request human approval before proposing implementation of:

* New product features not already listed in this brief or backlog.
* Notification integrations.
* Authentication or user accounts.
* Database migrations.
* Deployment or infrastructure changes.
* Security-sensitive behavior.
* New external services.
* Paid APIs.
* Major architecture changes.
* Changes to detector thresholds, scoring, severity, or event lifecycle.

Claude Implementer may do the following only for approved tasks:

* Implement exactly one approved task.
* Modify application code only within the task scope.
* Add or update tests.
* Run lint, typecheck, tests, and build commands.
* Update README/docs when behavior, setup, or architecture changes.
* Update `.agent/handoff.md`.
* Commit progress to an agent branch.
* Open or update a draft PR.

Claude Implementer must not:

* Implement proposed tasks that are not approved.
* Push directly to `main` or `master`.
* Merge PRs.
* Change auth, secrets, billing, deployment, database migrations, or notification integrations unless explicitly approved.
* Delete tests to make a build pass.
* Hide failing tests.
* Expand product scope beyond the approved task.

## Current priority for autonomous agents

For early testing of the two-agent system, prioritize low-risk improvements:

1. README and developer documentation improvements.
2. Frontend dashboard loading, empty, and error states.
3. Frontend visual clarity and mobile responsiveness.
4. Backend test coverage.
5. API error consistency.
6. Detector edge-case tests.
7. Architecture documentation.
8. Evaluation documentation clarity.

Avoid high-risk detector behavior changes until the agent workflow has proven reliable.

## Suggested first approved tasks

These are safe first tasks for testing the agent workflow:

1. Improve README setup and development commands.

Objective:
Make it easier for a new developer to run the project locally, run tests, run the frontend build, and understand the architecture.

Risk:
Low.

2. Improve dashboard empty/loading/error states.

Objective:
Make the dashboard more helpful when the API is loading, unavailable, or has no events/readings.

Risk:
Low to medium.

3. Add API contract tests for `/health`, `/readings`, and `/events`.

Objective:
Ensure the documented API shape remains stable.

Risk:
Low.

4. Add detector edge-case tests.

Objective:
Strengthen confidence around empty history, zero standard deviation, missing peers, missing forecast rows, and timezone-aware timestamps.

Risk:
Medium.

5. Improve event explanation display in the dashboard.

Objective:
Make event `reason` and `signal_values` easier to understand in the UI.

Risk:
Medium.

## Preferred implementation style

Backend:

* Keep functions small and testable.
* Prefer pure detector functions where possible.
* Keep I/O out of detector logic.
* Preserve timezone-aware UTC datetime handling.
* Use typed Pydantic models for API boundaries.
* Keep database writes explicit and easy to reason about.
* Avoid live network calls in tests.

Frontend:

* Keep dashboard components readable.
* Prefer simple, accessible UI over flashy visuals.
* Use existing stack and style conventions.
* Preserve relative API paths.
* Avoid adding frontend secrets or CORS complexity.
* Show clear loading, empty, and error states.
* Keep filters reflected in the URL if that is already the existing behavior.

Testing:

* Run backend tests with pytest.
* Run frontend typecheck, lint, and build when frontend changes.
* Mock upstream HTTP calls.
* Do not require real Open-Meteo calls in tests.
* Add tests for any behavior change.

Documentation:

* Update README when setup, commands, architecture, or user-visible behavior changes.
* Update evaluation docs only when evaluation behavior changes.
* Clearly label limitations and assumptions.
* Do not overstate detector accuracy.

## Useful commands

Backend / full project:

```bash
docker compose up --build
curl http://localhost:8000/health
pytest
ruff check .
mypy app
```

Frontend:

```bash
cd frontend
npm install
npm run typecheck
npm run lint
npm run build
```

Backfill / evaluation:

```bash
docker compose exec api python -m app.backfill --days 90 --chunk-days 31
python3 scripts/evaluate.py --source archive --start-date 2022-01-01 --end-date 2025-12-31
```

Note:
Commands may need adjustment based on the local environment. Agents should inspect the repository before running commands and should document any command that fails.

## Definition of done for agent tasks

A task is done only when:

* The approved scope is implemented.
* Relevant tests/checks were run.
* Failures are documented honestly.
* No forbidden area was modified.
* `.agent/handoff.md` is updated.
* Docs are updated if behavior/setup changed.
* A draft PR or clear commit summary exists.
* The implementation does not expand beyond the approved task.

## Human owner

The human owner is the final approver for:

* Product direction.
* New feature approval.
* High-risk changes.
* PR merge decisions.
* Deployment decisions.
