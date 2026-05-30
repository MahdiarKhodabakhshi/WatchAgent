# WatchAgent

WatchAgent is a Python 3.11+ service that polls Open-Meteo for Ottawa, Toronto, and Vancouver,
deduplicates hourly readings, detects notable weather events, persists data in SQLite, and exposes a
small HTTP API for health, readings, and events.

## What This Is

Open-Meteo updates current observations hourly, but a service may poll more frequently for
operational freshness. WatchAgent treats `(city, observation_ts)` as the identity of a reading, so
repeated polls of the same upstream observation do not duplicate database rows or events.

The event detector is the center of the project. It converts raw weather readings into explainable
event records such as rapid changes, sustained extremes, WMO severity jumps, comfort divergence, and
cross-city contrast. Every event contains the numeric signal values behind the decision plus a human
readable reason.

The `.cursor/` directory is included as a development-time artifact. It contains rules, one scoped
agent, and two executable skills for data analysis and replaying detector behavior.

## Architecture

```text
                                       +------------------------------+
                                       |       Open-Meteo API         |
                                       |  api.open-meteo.com/v1/...   |
                                       +--------------+---------------+
                                                      |
                                                      | HTTPS
                                                      v
+-------------------------------------------------------------------------+
|                       WatchAgent service (Docker)                       |
|                                                                         |
|   +----------------+                                                     |
|   | Async Poller   |  fetch concurrently, retry with backoff             |
|   | asyncio+httpx  |                                                     |
|   +--------+-------+                                                     |
|            | normalized reading                                          |
|            v                                                            |
|   +----------------+  unique(city, observation_ts)                       |
|   | Storage        |                                                     |
|   | SQLite+SQLA    |                                                     |
|   +--------+-------+                                                     |
|            | new reading + recent history                                |
|            v                                                            |
|   +----------------+  pure detect(reading, history, peers)               |
|   | Event Detector |                                                     |
|   +--------+-------+                                                     |
|            | events                                                      |
|            v                                                            |
|   +----------------+                                                     |
|   | Storage        |                                                     |
|   +--------+-------+                                                     |
|            |                                                            |
|            v                                                            |
|   +----------------+                                                     |
|   | FastAPI        |  /health  /readings  /events                       |
|   +----------------+                                                     |
+-------------------------------------------------------------------------+

                          .cursor/  development-time only
```

## Quickstart

```bash
cp .env.example .env
docker compose up --build
curl http://localhost:8000/health
```

The service starts a background poller by default. `ANTHROPIC_API_KEY` is not required for the
service; it is only used by the offline data-analysis Cursor skill.

If port 8000 is already in use, set `HOST_PORT` in `.env` before starting Compose.

### Backfill with historical data (fast testing)

If you want to exercise the detector on a large dataset without waiting for live polling, you can
backfill the database from the Open-Meteo archive API (hourly data).

Run this inside the running container:

```bash
docker compose exec api python -m app.backfill --days 90 --chunk-days 31
curl http://localhost:8000/health
curl "http://localhost:8000/events?limit=10"
```

Tip: set `ENABLE_POLLER=false` in `.env` while backfilling to avoid mixing live readings with the
historical load.

## API

### `GET /health`

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "readings_stored": 3,
  "events_stored": 1
}
```

### `GET /readings`

Query parameters:

- `city`: optional, one of `Ottawa`, `Toronto`, `Vancouver`
- `limit`: optional, default `50`, max `500`

```bash
curl "http://localhost:8000/readings?city=Toronto&limit=10"
```

```json
{
  "readings": [
    {
      "id": 1,
      "city": "Toronto",
      "observation_ts": "2026-05-27T18:00:00Z",
      "polled_at": "2026-05-27T18:05:00Z",
      "temperature_2m": 21.5,
      "apparent_temperature": 20.9,
      "precipitation": 0.0,
      "wind_speed_10m": 12.3,
      "weather_code": 1
    }
  ]
}
```

### `GET /events`

Query parameters:

- `city`: optional, one of `Ottawa`, `Toronto`, `Vancouver`
- `limit`: optional, default `50`, max `500`

```bash
curl "http://localhost:8000/events?city=Toronto&limit=10"
```

```json
{
  "events": [
    {
      "id": 1,
      "city": "Toronto",
      "event_ts": "2026-05-27T18:00:00Z",
      "created_at": "2026-05-27T18:05:01Z",
      "event_type": "rapid_change",
      "severity": "warning",
      "metric": "temperature_2m",
      "signal_values": {
        "value": 27.9,
        "mean": 22.4,
        "std": 1.8,
        "z_score": 3.1
      },
      "reason": "temperature 2m 27.9 is 3.1 sigma from Toronto's 24h mean of 22.4.",
      "supporting_reading_ids": [1, 2, 3]
    }
  ]
}
```

## Event Detection

Raw readings are most useful when filtered into a stream of moments worth attention. WatchAgent's
detection layer follows three rules.

**Per-city calibration.** Statistical detectors use each city's own rolling history, rather than a
global hardcoded weather threshold. This matters because a 5C temperature swing has different meaning
in Vancouver than in Ottawa.

**Defensibility.** Every event includes `signal_values` and a `reason` string that references those
numbers. The API should explain why an event fired without requiring a reviewer to inspect code.

**Cold-start safety.** Statistical detectors wait until at least 12 historical readings are
available. `wmo_transition` can run earlier because it compares categorical severity between the
current and previous reading.

The implemented event types are:

1. `rapid_change`: fires when temperature, wind, or precipitation is at least 2.5 standard
   deviations from the city's baseline. Severity becomes `severe` at 3.5 sigma.
2. `sustained_extreme`: fires when the current reading and previous two readings are all in the
   same 5th or 95th percentile tail.
3. `wmo_transition`: fires when WMO weather code category jumps by at least two severity levels.
4. `comfort_divergence`: fires when apparent temperature diverges from actual temperature beyond
   the city's baseline mean gap plus two standard deviations.
5. `cross_city_contrast`: fires when the current city-peer metric gap exceeds the 95th percentile
   of recent comparable gaps and also clears a metric-specific minimum gap.
6. `forecast_divergence`: fires when an observed reading diverges significantly from what was
   forecast for that hour — either a temperature miss ≥ 6°C or a WMO weather code jump of ≥ 2
   severity levels.

### Diurnal-aware baselines

A 24-hour rolling mean treats 3pm the same as 3am. In cities with large diurnal temperature swings,
this creates false positives every warm afternoon. `rapid_change` and `comfort_divergence` now
prefer a **same-local-hour baseline**: 14 days of readings at the same local hour (±1h, via
`zoneinfo`). If at least 7 same-hour samples exist, the detector uses that distribution; otherwise
it falls back to the rolling 24-hour (or 48-hour) window. Over real backfill data, 98% of
`rapid_change` events use the diurnal baseline (see [EVALUATION.md](EVALUATION.md)).

### Forecast reconciliation

During each poll, the poller fetches current conditions **and** hourly forecasts in a single
Open-Meteo API call. Forecasts for the next 3–12 hours are stored in a `forecasts` table with a
`UNIQUE(city, target_ts)` constraint — only the earliest lead-time forecast is kept per hour,
preserving the most informative prediction. When a reading arrives, the poller looks up whether a
forecast exists for that exact hour and, if so, passes it to the pure `detect_forecast_divergence`
detector. The feature ships behind `ENABLE_FORECAST_RECONCILIATION` (default true) so it can be
toggled off without code changes.

Implementation note: the plan describes a full historical pairwise distribution for
`cross_city_contrast`. The detector contract in the same plan passes only the triggering city's
history plus the latest peer readings. I preserved that pure function contract and compute a rolling
gap baseline from the triggering city's history against each latest peer value. I also added
metric-specific minimum gaps to avoid noisy alerts when the historical distribution is flat.

### Evaluation

Detector quality is documented in [EVALUATION.md](EVALUATION.md). It covers:

- **Labeled scenarios**: 18 synthetic test cases with exact ground truth, yielding 100% precision
  and recall. These run in CI as `tests/test_labeled_scenarios.py`.
- **Characterization over backfill**: event rates, z-score distribution, diurnal baseline split,
  and severity breakdown over ~90 days of real data. Honestly framed as characterization (no ground
  truth), not accuracy claims.

## Cursor Setup

Rules live in `.cursor/rules/`:

- `event-record-contract.mdc`: requires complete, explainable Event records.
- `poller-failure-policy.mdc`: keeps the poller alive through upstream failures.
- `detection-purity.mdc`: prevents I/O and clock access inside detection functions.
- `time-handling.mdc`: requires timezone-aware UTC datetimes.
- `test-mocking.mdc`: requires mocked upstream HTTP calls in tests.

The custom agent is `.cursor/agents/event-logic-reviewer.md`. It reviews detector changes against
edge cases such as empty history, zero standard deviation, cold-start boundaries, sensor anomalies,
missing peers, and timezone-naive datetimes.

Skills live in `.cursor/skills/`:

- `data-analysis`: answers natural-language questions about stored readings and events. Implements
  three agentic design patterns:
  - **Tool Use**: a bounded ReAct loop (≤ 6 steps) with deterministic SQLAlchemy tools.
  - **Reflection**: a verification pass after the main loop checks every numeric claim against the
    tool-result trace, returning corrections if any (1 extra LLM call, 7 max total).
  - **Grounded NL digest** (`digest.py`): gathers facts deterministically from the DB, then has the
    LLM render only those facts into prose. Both the prose and raw facts are returned for
    verification.
  - **Eval suite** (`evals/`): 8 questions graded against a fixed seed database with structural and
    value checks (no LLM grading). Manual only — requires an API key, never in CI.
- `replay-detection`: re-runs current detection logic over stored readings without writing to the
  database.

## Tech Choices

**Python 3.11+.** The challenge targets Python 3.11+. This implementation is tested locally with
Python 3.12 and CI pins Python 3.11.

**FastAPI.** FastAPI gives typed response models, async lifespan hooks for the poller, and generated
OpenAPI docs at `/docs`.

**httpx.** The poller uses one async `httpx.AsyncClient` per loop for connection reuse and
concurrent city fetches.

**SQLite + SQLAlchemy.** SQLite is the right scale for three cities and hourly upstream updates. It
keeps deployment simple and persists through a Docker volume. SQLAlchemy provides models, sessions,
constraints, and a clear path to Postgres if write volume grows.

**asyncio scheduling.** A dedicated `asyncio.create_task()` starts inside the FastAPI lifespan. This
is enough for a single polling loop and avoids Celery, Redis, or APScheduler complexity.

**structlog.** Logs are structured JSON with a `trace_id` per poll cycle.

**pytest + respx.** Tests cover deduplication, Open-Meteo parsing, event detection, and API shape.
`respx` prevents real network calls in tests.

## Implementation Notes

Two pragmatic additions differ from the literal plan:

- `ENABLE_POLLER` in `.env.example` defaults to `true`, but tests set it to `false` so FastAPI can
  start without live Open-Meteo calls.
- The Docker builder stage copies `app/` before `pip install .` because Python package installation
  needs package files present. The plan's shorter snippet copied only `pyproject.toml`.
- Compose uses `${HOST_PORT:-8000}:8000` so the default matches the plan while still allowing local
  smoke tests on another port when 8000 is occupied.

## Tests

```bash
pip install -e ".[dev]"
pytest -q
ruff check app tests
```

CI runs the same lint and test commands, then validates `docker build`.

## Design Decisions & What I Deliberately Didn't Build

**Deterministic detection over LLM detection.** The detectors are pure functions with no LLM in the
loop. This makes every event reproducible, testable, and explainable without API costs or latency.
The LLM is reserved for the data-analysis skill where natural-language flexibility adds value and
the grounded-generation pattern prevents hallucination.

**No multi-agent runtime.** A single-agent ReAct loop with bounded steps (6 + 1 reflection) is
sufficient for the data-analysis use case. Multi-agent orchestration would add complexity without
improving answer quality for the bounded set of SQL-backed tools.

**No RAG.** The data lives in a structured SQLite database, not a document corpus. SQL queries over
typed columns are more precise than embedding-based retrieval for questions like "max temperature
in Vancouver." RAG would be appropriate if the system needed to search unstructured text, but that
is not the case here.

**Feature-flag forecasting.** Forecast reconciliation is gated behind
`ENABLE_FORECAST_RECONCILIATION` rather than hard-coded. This lets operators disable it without a
code change — useful during initial deployment or if the forecast API becomes unreliable. The
forecasts table uses a `UNIQUE(city, target_ts)` constraint with an earliest-lead-wins policy so
the most informative forecast is always the one compared against reality.

**No new columns on existing tables.** The `forecasts` table is new, but `readings` and `events`
are unchanged. This avoids the need for schema migrations on a deployed database, since the project
does not include Alembic.

**Honest evaluation framing.** `EVALUATION.md` separates ground-truthed labeled scenarios (exact
precision/recall) from backfill characterization (event rates with no ground truth). Claiming
accuracy on unlabeled data would be misleading; the split makes the distinction explicit.

## Things I Would Do With More Time

- Add Alembic migrations once the schema evolves beyond the initial two tables.
- Add an endpoint for aggregate event counts by city and type.
- Store richer peer history for exact pairwise cross-city distributions.
- Add a small seed-data command for local demos.
- Add a CI smoke test that starts the container and calls `/health`.
