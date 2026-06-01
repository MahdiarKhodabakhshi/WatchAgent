# WatchAgent

WatchAgent is a Python 3.11+ service that polls Open-Meteo for Ottawa, Toronto, and
Vancouver, deduplicates hourly readings, detects notable weather incidents, stores them in
SQLite, and exposes a small FastAPI API plus a read-only dashboard.

The project intentionally uses deterministic weather logic, not an LLM, for detection. The
interesting part of the repo is the event-design layer: local-hour climatology, pure
detectors, DB-backed lifecycle collapse, and replayable calibration evidence.

## Quickstart

No API keys are required for the service. Open-Meteo is the only live weather provider, and
the committed `app/data/climatology.json` artifact is loaded from disk at startup. The app
does not fetch archive data during boot.

```bash
git clone <repo-url>
cd watchagent
cp .env.example .env
docker compose up --build
```

In another shell:

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/readings?limit=5"
curl "http://localhost:8000/events?limit=5"
```

`/readings` and `/events` may be empty on a fresh database until the poller stores its first
Open-Meteo response, but the endpoints should respond without credentials. If port 8000 is
already in use, set `HOST_PORT` in `.env` before starting Compose.

The dashboard window control includes fixed 24-hour, 7-day, and 14-day views plus a custom
1-60 day view. Custom windows are reflected in the URL as `?window=custom&days=30`.

### Recreate A Local Dev DB

This repo does not use Alembic. Schema changes are additive in SQLAlchemy models, and a clean
clone creates the right SQLite tables automatically. Existing local databases from an older
schema should be recreated:

```bash
docker compose down
rm -f data/watchagent.db data/watchagent.db-*
docker compose up --build
```

### Backfill For Local Testing

Backfill uses Open-Meteo archive data and writes to the local database. It does not use ECCC
or credentials.

```bash
docker compose exec api python -m app.backfill --days 90 --chunk-days 31
curl "http://localhost:8000/events?limit=10"
```

Set `ENABLE_POLLER=false` in `.env` while backfilling if you want to avoid mixing live and
historical readings.

### Rebuild Climatology

Runtime code loads the committed climatology artifact. To refresh it offline:

```bash
python3 scripts/build_climatology.py --start-date 2015-01-01 --end-date 2021-12-31
```

The committed artifact is intentionally fit on a historical training window. Evaluation replays
use a later disjoint test window so detector-rate claims are not measured on the same years used
to define seasonal baselines.

## Architecture

```text
1. Ingest + storage
   Open-Meteo current/forecast pulls and archive backfill -> SQLite readings/forecasts
        |
        v
2. Feature + detector layer
   local-hour climatology, MAD scales, pure DetectorContext -> EventCandidate detectors
        |
        v
3. Lifecycle + scoring
   stable dedupe keys, hysteresis, peak tracking, priority_score, severity from score
        |
        v
4. Serving + replay
   /health, /readings, /events, dashboard, Cursor skills, scripts/evaluate.py
```

Important boundaries:

- Live pipeline uses Open-Meteo only.
- Detectors are pure functions of `DetectorContext`; no DB, network, or clock reads.
- `/health`, `/readings`, and `/events` contracts are additive.
- Event lifecycle state is DB-backed in `incident_states`, so incidents survive poller
  restarts.

## API

### `GET /health`

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
- `start`, `end`: optional timezone-aware datetimes
- `limit`: optional, default `50`, max `500`

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
      "weather_code": 1,
      "surface_pressure": 1004.2,
      "pressure_msl": 1011.8,
      "relative_humidity_2m": 71,
      "dew_point_2m": 16.2,
      "wind_gusts_10m": 34.0,
      "cloud_cover": 88,
      "snowfall": 0.0,
      "snow_depth": null
    }
  ]
}
```

Open-Meteo occasionally omits fields by city/hour/model. Enriched fields are nullable by
design.

### `GET /events`

Query parameters:

- `city`: optional, one of `Ottawa`, `Toronto`, `Vancouver`
- `start`, `end`: optional timezone-aware datetimes
- `limit`: optional, default `50`, max `500`

The feed sorts by `priority_score` first, then recency. Existing fields remain present, and
lifecycle/scoring fields are additive.

```json
{
  "events": [
    {
      "id": 1,
      "city": "Toronto",
      "event_ts": "2024-07-16T17:00:00Z",
      "created_at": "2024-07-16T17:05:01Z",
      "event_type": "heavy_rain_burst",
      "severity": "severe",
      "metric": "precipitation",
      "signal_values": {
        "precipitation_mm": 4.4,
        "accumulation_mm": 11.0,
        "wet_hour_p95_mm": 10.0
      },
      "reason": "Toronto's rain accumulation reached 11.0 mm over 6h during a wet hour.",
      "supporting_reading_ids": [101, 102, 103],
      "status": "ongoing",
      "onset_ts": "2024-07-16T17:00:00Z",
      "peak_ts": "2024-07-16T17:00:00Z",
      "resolved_ts": null,
      "priority_score": 67.0,
      "confidence": 0.95,
      "dedupe_key": "Toronto|heavy_rain_burst|precipitation",
      "evidence": {
        "lifecycle": {
          "peak_strength": 11.0,
          "clear_count": 0
        }
      }
    }
  ]
}
```

## Event Design

WatchAgent detects incidents, not one row per noisy trigger. A detector emits
`EventCandidate` objects from a pure `DetectorContext`; lifecycle then opens, updates, or
resolves one persistent `Event` per stable `dedupe_key`.

Scoring is centralized in `app/detection/scoring.py`. Each detector provides normalized
score inputs such as rarity, magnitude, persistence, compound evidence, forecast surprise,
spatial separation, and confidence. `priority_score` is a weighted 0-100 value, and stored
`severity` is derived from score: `<30 info`, `30-59 warning`, `>=60 severe`.

### Detector Catalog

| detector | phenomenon | statistic | initial threshold and calibration hypothesis |
|---|---|---|---|
| `temperature_shock` | Sudden temperature jumps that are unusual for that city and hour. | Local-hour `z_hod` plus a 3-hour temperature derivative. | `abs(z_hod) >= 3.0` and `abs(delta_3h) >= 5C`. This preserves the useful diurnal-aware baseline from the old rapid-change rule while filtering routine afternoon warming. |
| `pressure_plunge` | Pressure falls that often precede stormy conditions. | 3-hour sea-level pressure fall, checked against local pressure behavior and confirmed by wind/gust rise. | At least a 6 hPa fall plus wind corroboration. Replay showed weaker falls were ordinary weather noise, so the threshold keeps only sharper, compound signals. |
| `warm_spell` | Persistent locally extreme warmth. | Temperature `z_hod` above the local-hour climatology, collapsed by lifecycle hysteresis. | `z_hod >= 3.0`. This replaces spammy `sustained_extreme`; in the 2022-2025 test replay, 67,513 old raw firings became 172 warm/cold spell incidents. |
| `cold_spell` | Persistent locally extreme cold. | Temperature `z_hod` below the local-hour climatology, collapsed by lifecycle hysteresis. | `z_hod <= -3.0`. The same spell hypothesis as warm spells, with score magnitude lifting extreme cold outbreaks to severe. |
| `heavy_rain_burst` | Flash-flood style rain bursts and short accumulations. | Current hour must be wet; compare wet-hour amount and 6-hour accumulation against wet-hour-only baselines. | Wet current hour, hourly amount at least `max(wet p95, 10 mm)`, or 6-hour accumulation at least 10 mm. The wet/dry split avoids zero-dominated medians; Toronto/Ottawa flood spot checks drove the accumulation scoring. |
| `wind_gust_burst` | Locally unusual gusts with operational damage potential. | `wind_gusts_10m` anomaly against local-hour climatology, with an ECCC-scale absolute gust anchor. | `z_hod >= 3.2` or gust around 90 km/h. The replay rate landed near other direct hazard detectors after raising the z threshold. |
| `heat_stress` | Dangerous heat load from temperature plus moisture. | Humidex from temperature and dew point. | Humidex `>= 38`, with Humidex 40 as a strong anchor. Full-season replay produced non-trivial but not constant summer incidents. |
| `cold_stress` | Dangerous wind chill from cold plus wind. | Wind Chill Index from temperature and wind speed. | Wind chill `<= -25` with valid cold/wind inputs. City-center archive data made `-30` effectively dead, so `-25` keeps real winter stress visible. |
| `forecast_bust` | A live forecast miss large enough to matter operationally. | `abs(observed - stored_forecast) / max(global rolling MAE, metric_floor)`. | Normalized error `>= 2.5` with at least 3 recent obs/forecast comparison pairs. Archive replay has no historical forecast pairs, so this is exercised by unit/labeled tests and live DB operation. |
| `spatial_anomaly` | One city is anomalous relative to its own climate and its peers. | Own-city `z_hod`, then gap from median peer `z_hod` across temperature, gust, and pressure. | Own `abs(z_hod) >= 3.0` and peer z-gap `>= 5.0`. This prevents "normal Vancouver mildness while Ottawa freezes" from counting as an event; geography alone is not a hazard. |

`wmo_transition` is no longer a primary event. WMO weather-code tier changes are treated as
supporting evidence where useful instead of a spammy feed item.

### Robust Statistics

- **Median/MAD over mean/std**: weather tails are heavy and seasonal; one storm should not drag
  the baseline the way a mean and standard deviation can.
- **MAD floor**: `max(1.4826 * MAD, metric_epsilon)` prevents zero-variance buckets from
  creating infinite z-scores.
- **Local-hour buckets**: climatology is keyed by `(city, month, local_hour)` using each
  city's timezone, avoiding UTC-smearing of the diurnal cycle.
- **Precip occurrence vs amount**: dry hours are modeled separately from wet-hour amount
  percentiles, so heavy rain is not compared to a zero-dominated median.
- **Lifecycle hysteresis**: incidents open after enter criteria and resolve only after clear
  criteria, which debounces borderline oscillation and preserves stable onset/peak times.

### Confidence

Candidates carry a normalized confidence score. Confidence is lowered when the feature layer
uses fallback baselines because a `(city, month, local_hour)` bucket is thin, when peer data is
missing for spatial comparison, when forecast-bust lacks enough rolling MAE pairs, or when key
weather variables are unavailable. Low confidence suppresses score rather than changing the API
shape.

### Evaluation Evidence

The replayable evidence lives in [EVALUATION.md](EVALUATION.md):

```bash
python3 scripts/evaluate.py --source archive --start-date 2022-01-01 --end-date 2025-12-31
```

Current DS-1 replay uses the committed 2015-2021 climatology artifact as training data and
measures rates on the disjoint 2022-2025 archive test window. This removes train/test leakage,
while acknowledging that a fixed historical climate baseline can drift as climate and observing
systems change.

- Legacy raw detector firings: **127,164**
- Native lifecycle incidents: **1,121**
- Overall rate: **0.256 incidents/city-day**
- Raw-firing to incident collapse ratio: **4.33x** on native candidates
- `sustained_extreme` replacement: **67,513 raw firings -> 172 spell incidents**

Per-type after-state:

| detector_type | incidents | per_city_day |
|---|---:|---:|
| `temperature_shock` | 21 | 0.005 |
| `pressure_plunge` | 52 | 0.012 |
| `warm_spell` | 101 | 0.023 |
| `cold_spell` | 71 | 0.016 |
| `heavy_rain_burst` | 333 | 0.076 |
| `wind_gust_burst` | 334 | 0.076 |
| `heat_stress` | 53 | 0.012 |
| `cold_stress` | 70 | 0.016 |
| `forecast_bust` | 0 | 0.000 |
| `spatial_anomaly` | 86 | 0.020 |

Forecast-bust is zero in archive replay because Open-Meteo archive provides observations, not
the forecasts issued at the time. The detector fires in
`tests/test_native_detectors.py::test_forecast_bust_fires_on_error_over_rolling_mae` and in the
labeled `forecast_bust_simple_mae` scenario; live operation compares readings with stored
forecast rows from the current/forecast pull.

Known-event spot checks from the same replay:

| documented event | date | incident |
|---|---|---|
| Toronto heavy rainfall/flooding | 2024-07-16 | `heavy_rain_burst / precipitation`, priority 67.0, severe |
| Vancouver January deep freeze | 2024-01-12 | `cold_spell / temperature_2m`, priority 70.0, severe |
| Ottawa severe thunderstorm/outages | 2023-06-26 | `heavy_rain_burst / precipitation`, priority 66.2, severe |

### Deliberately Out Of Scope

- **EVT/GPD**: attractive for tail modeling, but too much calibration surface for this take-home.
- **BOCPD, ADWIN, PELT**: change-point tools were cut to keep behavior explainable and testable
  with hourly weather data.
- **Isolation Forest**: would obscure why an event fired and require broader validation data.
- **LSTM or other sequence models**: not justified for three cities, limited labels, and a
  deterministic operational feed.
- **Live ECCC alerts**: ECCC can be useful as offline weak labels, but the live pipeline remains
  Open-Meteo only.
- **Lead-conditioned forecast bust**: forecast storage still keeps one forecast per target time;
  lead-binned forecast-error calibration is documented future work.

## Cursor Setup

The `.cursor/` directory is a development-time artifact for reviewing and replaying the actual
WatchAgent design.

Rules:

- `.cursor/rules/detection-purity.mdc`: detectors are pure
  `DetectorContext -> list[EventCandidate]` functions.
- `.cursor/rules/event-record-contract.mdc`: candidates and stored events must remain
  explainable, scored, and additive.
- `.cursor/rules/poller-failure-policy.mdc`: the poller logs and continues through upstream
  failures.
- `.cursor/rules/time-handling.mdc`: all datetimes are timezone-aware UTC at storage/API
  boundaries.
- `.cursor/rules/test-mocking.mdc`: API-touching tests mock network calls.

Agent:

- `.cursor/agents/event-logic-reviewer.md`: reviews detector and lifecycle changes against
  cold-start behavior, missing variables, confidence, dedupe keys, scoring inputs, and replay
  evidence.

Skills:

- `.cursor/skills/data-analysis`: `python3 .cursor/skills/data-analysis/analyze.py "question"`
  answers read-only questions against the local DB; `digest.py` produces grounded event briefs.
- `.cursor/skills/replay-detection`: `python3 .cursor/skills/replay-detection/replay.py --limit 100`
  replays current candidates and lifecycle state over stored readings without writing events.
- `.cursor/skills/explain-event`: `python3 .cursor/skills/explain-event/explain.py --event-id 1`
  prints the stored lifecycle, score, evidence, and related reading context for one event.

The optional LLM-backed data-analysis skill requires `ANTHROPIC_API_KEY`. The WatchAgent
service, tests, and Docker startup do not.

## Development

```bash
python3 -m pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check app tests scripts
npm --prefix frontend install
npm --prefix frontend run typecheck
npm --prefix frontend run lint
docker compose build
```

CI runs lint, tests, frontend checks, and Docker build. Tests that touch Open-Meteo use mocks;
no credentials are committed or required.

## Tech Choices

- **FastAPI** for typed response models, async lifespan hooks, and generated `/docs`.
- **httpx + asyncio** for concurrent Open-Meteo polling with retry/backoff.
- **SQLite + SQLAlchemy** because three cities and hourly readings do not need distributed
  infrastructure.
- **React dashboard** served from the same FastAPI origin, avoiding CORS and frontend secrets.
- **structlog** for JSON logs with poll-cycle trace IDs.
- **pytest + respx** for deterministic storage, API, and Open-Meteo tests.

## Future Work

- Add Alembic once schema evolution needs non-additive migrations.
- Add CI smoke coverage that starts the container and calls `/health`.
- Add lead-binned forecast-bust calibration once historical forecast pairs are available.
- Add an aggregate `/event-counts` endpoint for dashboard filtering.
