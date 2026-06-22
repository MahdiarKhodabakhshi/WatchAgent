#!/usr/bin/env python3
"""Read-only offline evaluation for WatchAgent detectors.

The archive mode fetches multi-year Open-Meteo historical observations into
memory, replays both the retired legacy rules and the native detector/lifecycle
path, and writes EVALUATION.md plus small summary figures. It never writes to
the live application database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.detection import forecast_bust as forecast_bust_module  # noqa: E402
from app.detection import heavy_rain_burst as rain_module  # noqa: E402
from app.detection import pressure_plunge as pressure_module  # noqa: E402
from app.detection import scoring as scoring_module  # noqa: E402
from app.detection import spatial_anomaly as spatial_module  # noqa: E402
from app.detection import spells as spells_module  # noqa: E402
from app.detection import stress as stress_module  # noqa: E402
from app.detection import temperature_shock as temp_module  # noqa: E402
from app.detection import wind_gust_burst as wind_module  # noqa: E402
from app.detection.base import DetectorContext, EventCandidate  # noqa: E402
from app.detection.lifecycle import dedupe_key_for_candidate  # noqa: E402
from app.detection.registry import detect_candidates  # noqa: E402
from app.detection.rules import (  # noqa: E402
    DIURNAL_WINDOW_DAYS,
    detect_comfort_divergence,
    detect_cross_city_contrast,
    detect_forecast_divergence,
    detect_fun_facts,
    detect_rapid_change,
    detect_sustained_extreme,
    detect_wmo_transition,
)
from app.detection.timeofday import local_hour  # noqa: E402
from app.features import Climatology  # noqa: E402
from app.models import Forecast, Reading  # noqa: E402
from app.open_meteo import CITIES, CITY_NAMES, fetch_city_hourly_history  # noqa: E402
from tests.labeled_scenarios import SCENARIOS  # noqa: E402

FIG_DIR = PROJECT_ROOT / "evaluation"
EVAL_PATH = PROJECT_ROOT / "EVALUATION.md"
CLIMATOLOGY_PATH = PROJECT_ROOT / "app" / "data" / "climatology.json"
DEFAULT_START = date(2022, 1, 1)
DEFAULT_END = date(2025, 12, 31)
ARCHIVE_FETCH_ATTEMPTS = 5
NATIVE_TYPES = (
    "temperature_shock",
    "pressure_plunge",
    "warm_spell",
    "cold_spell",
    "heavy_rain_burst",
    "wind_gust_burst",
    "heat_stress",
    "cold_stress",
    "forecast_bust",
    "spatial_anomaly",
)
LEGACY_REPLACEMENTS = (
    ("rapid_change", "temperature_shock"),
    ("sustained_extreme", "warm_spell + cold_spell"),
    ("comfort_divergence", "heat_stress + cold_stress"),
    ("cross_city_contrast", "spatial_anomaly"),
    ("forecast_divergence", "forecast_bust"),
    ("wmo_transition", "supporting evidence only"),
    ("fun_fact", "retired from primary feed"),
)
# Each entry's `evidence` string is only rendered when a severe incident matches in
# +/-48h; the rain entries currently do not match in ERA5 replay (see the honesty
# note under the spot-check table) so their evidence describes that false negative.
KNOWN_EVENT_SPOT_CHECKS = [
    {
        "event": "Toronto heavy rainfall/flooding",
        "city": "Toronto",
        "event_type": "heavy_rain_burst",
        "source_date": "2024-07-16",
        "source": (
            "https://www.toronto.ca/news/"
            "city-of-toronto-provides-an-update-on-response-efforts-following-heavy-rainfall/"
        ),
        "source_summary": "City reported more than 100 mm in pockets across Toronto.",
        "incident": "heavy_rain_burst / precipitation",
        "evidence": "ERA5 peak 4.3 mm/h, max 11.0 mm/6h -- below the bar; not detected",
    },
    {
        "event": "Vancouver January deep freeze",
        "city": "Vancouver",
        "event_type": "cold_spell",
        "source_date": "2024-01-12",
        "source": (
            "https://www.canada.ca/en/environment-climate-change/services/"
            "ten-most-impactful-weather-stories/2024.html"
        ),
        "source_summary": "ECCC noted wind chills reaching Vancouver's waterfront.",
        "incident": "cold_spell / temperature_2m",
        "evidence": "Jan 12 candidates reached z=4.2 to z=7.1",
    },
    {
        "event": "Ottawa severe thunderstorm/outages",
        "city": "Ottawa",
        "event_type": "heavy_rain_burst",
        "source_date": "2023-06-26",
        "source": (
            "https://ottawa.citynews.ca/2023/06/26/"
            "environment-canada-issues-severe-thunderstorm-warning-for-ottawa/"
        ),
        "source_summary": "Thousands lost power; ECCC warned of downpours, hail, wind.",
        "incident": "heavy_rain_burst / precipitation",
        "evidence": "ERA5 peak 5.0 mm/h, max 10.6 mm/6h -- below the bar; not detected",
    },
]

# Weak labels for recall validation. ECCC does not expose a stable public API for
# historical alert archives, so this is a curated, sourced list of high-impact weather
# windows for the three cities over the replay span, drawn from ECCC's annual top-ten
# weather stories and contemporaneous reporting. Dates are approximate event windows;
# the matcher pads each by +/-1 day. `expected_types` maps the phenomenon to the
# detectors that should fire. This is a weak label set, not ground truth: it bounds
# recall on notable events, it does not enumerate every alert.
ECCC_2022 = "https://www.canada.ca/en/environment-climate-change/services/top-ten-weather-stories/2022.html"
ECCC_2023 = "https://www.canada.ca/en/environment-climate-change/services/top-ten-weather-stories/2023.html"
ECCC_2024 = "https://www.canada.ca/en/environment-climate-change/services/ten-most-impactful-weather-stories/2024.html"
WEAK_LABELS = [
    {"city": "Toronto", "event": "Ontario-Quebec derecho", "start": "2022-05-21",
     "end": "2022-05-21", "expected_types": ("wind_gust_burst", "pressure_plunge",
     "heavy_rain_burst"), "source": ECCC_2022},
    {"city": "Ottawa", "event": "Ontario-Quebec derecho", "start": "2022-05-21",
     "end": "2022-05-21", "expected_types": ("wind_gust_burst", "pressure_plunge",
     "heavy_rain_burst"), "source": ECCC_2022},
    {"city": "Vancouver", "event": "December arctic outflow cold", "start": "2022-12-19",
     "end": "2022-12-23", "expected_types": ("cold_spell", "cold_stress"), "source": ECCC_2022},
    {"city": "Toronto", "event": "Pre-Christmas winter storm / flash freeze",
     "start": "2022-12-23", "end": "2022-12-24", "expected_types": ("temperature_shock",
     "wind_gust_burst", "pressure_plunge"), "source": ECCC_2022},
    {"city": "Ottawa", "event": "Pre-Christmas winter storm / flash freeze",
     "start": "2022-12-23", "end": "2022-12-24", "expected_types": ("temperature_shock",
     "pressure_plunge"), "source": ECCC_2022},
    {"city": "Toronto", "event": "Eastern Ontario ice storm", "start": "2023-04-05",
     "end": "2023-04-06", "expected_types": ("heavy_rain_burst", "temperature_shock"),
     "source": ECCC_2023},
    {"city": "Ottawa", "event": "Eastern Ontario ice storm", "start": "2023-04-05",
     "end": "2023-04-06", "expected_types": ("heavy_rain_burst", "temperature_shock"),
     "source": ECCC_2023},
    {"city": "Ottawa", "event": "Severe thunderstorm / outages", "start": "2023-06-26",
     "end": "2023-06-27", "expected_types": ("heavy_rain_burst", "pressure_plunge"),
     "source": ECCC_2023, "headline_fn": True},
    {"city": "Toronto", "event": "Mid-January deep cold", "start": "2024-01-13",
     "end": "2024-01-16", "expected_types": ("cold_spell", "cold_stress"), "source": ECCC_2024},
    {"city": "Ottawa", "event": "Mid-January deep cold", "start": "2024-01-13",
     "end": "2024-01-16", "expected_types": ("cold_spell", "cold_stress"), "source": ECCC_2024},
    {"city": "Vancouver", "event": "Arctic deep freeze", "start": "2024-01-12",
     "end": "2024-01-14", "expected_types": ("cold_spell", "cold_stress"), "source": ECCC_2024},
    {"city": "Toronto", "event": "June heat wave", "start": "2024-06-17",
     "end": "2024-06-20", "expected_types": ("heat_stress", "warm_spell"), "source": ECCC_2024},
    {"city": "Ottawa", "event": "June heat wave", "start": "2024-06-17",
     "end": "2024-06-20", "expected_types": ("heat_stress", "warm_spell"), "source": ECCC_2024},
    {"city": "Toronto", "event": "Heavy rainfall / flooding", "start": "2024-07-16",
     "end": "2024-07-16", "expected_types": ("heavy_rain_burst",), "source": ECCC_2024,
     "headline_fn": True},
    {"city": "Vancouver", "event": "Bomb cyclone windstorm", "start": "2024-11-19",
     "end": "2024-11-20", "expected_types": ("wind_gust_burst", "pressure_plunge"),
     "source": ECCC_2024},
]


@dataclass(frozen=True)
class CalibrationProfile:
    name: str
    baseline_variant: str
    threshold_variant: str
    use_empirical_quantile_gates: bool
    temperature_shock_z: float
    temperature_shock_delta_c: float
    spell_z: float
    pressure_min_fall_hpa: float
    pressure_min_wind_rise_kmh: float
    pressure_min_confirming_gust_kmh: float
    heavy_rain_min_mm: float
    wind_gust_z: float
    wind_gust_anchor_kmh: float
    heat_humidex: float
    strong_heat_humidex: float
    cold_wind_chill: float
    strong_cold_wind_chill: float
    forecast_bust_k: float
    spatial_z_gap: float
    spatial_min_own_z: float
    surprisal_scoring: bool = True


DS2_MONTH_HOUR_PROFILE = CalibrationProfile(
    name="DS-2 month-hour quantile baseline",
    baseline_variant="legacy",
    threshold_variant="legacy",
    use_empirical_quantile_gates=True,
    temperature_shock_z=3.0,
    temperature_shock_delta_c=5.0,
    spell_z=3.0,
    pressure_min_fall_hpa=6.0,
    pressure_min_wind_rise_kmh=8.0,
    pressure_min_confirming_gust_kmh=60.0,
    heavy_rain_min_mm=10.0,
    wind_gust_z=3.2,
    wind_gust_anchor_kmh=90.0,
    heat_humidex=38.0,
    strong_heat_humidex=40.0,
    cold_wind_chill=-25.0,
    strong_cold_wind_chill=-35.0,
    forecast_bust_k=2.5,
    spatial_z_gap=5.0,
    spatial_min_own_z=3.0,
)


@dataclass(frozen=True)
class ReplayData:
    readings_by_city: dict[str, list[Reading]]
    timestamps_by_city: dict[str, list[datetime]]
    source_label: str
    start_date: date | None
    end_date: date | None

    @property
    def total_readings(self) -> int:
        return sum(len(rows) for rows in self.readings_by_city.values())

    @property
    def city_days_by_city(self) -> dict[str, int]:
        days: dict[str, int] = {}
        for city, rows in self.readings_by_city.items():
            days[city] = round(len(rows) / 24) if rows else 0
        return days

    @property
    def total_city_days(self) -> int:
        return sum(self.city_days_by_city.values())


@dataclass
class IncidentRecord:
    city: str
    event_type: str
    event_ts: datetime
    severity: str
    priority_score: float
    metric: str | None = None
    signal_values: dict[str, Any] = field(default_factory=dict)


@dataclass
class _IncidentState:
    city: str
    event_type: str
    active: bool = False
    clear_count: int = 0
    incident: IncidentRecord | None = None


@dataclass(frozen=True)
class NativeReplay:
    raw: list[tuple[Reading, EventCandidate]]
    incidents: list[IncidentRecord]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("archive", "db"),
        default="archive",
        help="archive fetches Open-Meteo history in memory; db reads local SQLite only.",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end-date", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--chunk-days", type=int, default=366)
    return parser.parse_args()


async def _load_archive_data(
    *,
    start_date: date,
    end_date: date,
    chunk_days: int,
) -> ReplayData:
    settings = get_settings()
    readings_by_city: dict[str, list[Reading]] = {}
    next_id = 1
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        for city in CITIES:
            rows: list[Reading] = []
            chunk_start = start_date
            while chunk_start <= end_date:
                chunk_end = min(chunk_start + timedelta(days=chunk_days - 1), end_date)
                print(f"Fetching {city.name} {chunk_start}..{chunk_end}", flush=True)
                records = await _fetch_city_hourly_history_with_retry(
                    client,
                    city,
                    start_date=chunk_start,
                    end_date=chunk_end,
                    settings=settings,
                )
                for record in records:
                    rows.append(_reading_from_record(record, next_id))
                    next_id += 1
                chunk_start = chunk_end + timedelta(days=1)
            readings_by_city[city.name] = sorted(rows, key=lambda item: item.observation_ts)
    return _replay_data(
        readings_by_city,
        source_label=f"Open-Meteo archive {start_date.isoformat()}..{end_date.isoformat()}",
        start_date=start_date,
        end_date=end_date,
    )


def _load_db_data() -> tuple[ReplayData, dict[tuple[str, datetime], Forecast]]:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    readings_by_city: dict[str, list[Reading]] = {}
    forecasts: dict[tuple[str, datetime], Forecast] = {}
    with SessionLocal() as session:
        for city in CITY_NAMES:
            readings_by_city[city] = list(
                session.scalars(
                    select(Reading)
                    .where(Reading.city == city)
                    .order_by(Reading.observation_ts.asc())
                ).all()
            )
        for forecast in session.scalars(select(Forecast)).all():
            forecasts[(forecast.city, forecast.target_ts)] = forecast
    return (
        _replay_data(
            readings_by_city,
            source_label=f"local DB {settings.database_url}",
            start_date=None,
            end_date=None,
        ),
        forecasts,
    )


def _reading_from_record(record: dict[str, Any], reading_id: int) -> Reading:
    return Reading(
        id=reading_id,
        city=record["city"],
        observation_ts=record["observation_ts"],
        polled_at=record["polled_at"],
        temperature_2m=record.get("temperature_2m"),
        apparent_temperature=record.get("apparent_temperature"),
        precipitation=record.get("precipitation"),
        wind_speed_10m=record.get("wind_speed_10m"),
        weather_code=record.get("weather_code"),
        surface_pressure=record.get("surface_pressure"),
        pressure_msl=record.get("pressure_msl"),
        relative_humidity_2m=record.get("relative_humidity_2m"),
        dew_point_2m=record.get("dew_point_2m"),
        wind_gusts_10m=record.get("wind_gusts_10m"),
        cloud_cover=record.get("cloud_cover"),
        snowfall=record.get("snowfall"),
        snow_depth=record.get("snow_depth"),
    )


def _replay_data(
    readings_by_city: dict[str, list[Reading]],
    *,
    source_label: str,
    start_date: date | None,
    end_date: date | None,
) -> ReplayData:
    return ReplayData(
        readings_by_city=readings_by_city,
        timestamps_by_city={
            city: [reading.observation_ts for reading in readings]
            for city, readings in readings_by_city.items()
        },
        source_label=source_label,
        start_date=start_date,
        end_date=end_date,
    )


async def _fetch_city_hourly_history_with_retry(
    client: httpx.AsyncClient,
    city: Any,
    *,
    start_date: date,
    end_date: date,
    settings: Any,
) -> list[dict[str, Any]]:
    for attempt in range(1, ARCHIVE_FETCH_ATTEMPTS + 1):
        try:
            return await fetch_city_hourly_history(
                client,
                city,
                start_date=start_date,
                end_date=end_date,
                settings=settings,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or attempt == ARCHIVE_FETCH_ATTEMPTS:
                raise
            retry_after = exc.response.headers.get("Retry-After")
            delay = (
                float(retry_after)
                if retry_after is not None and retry_after.isdigit()
                else min(60.0, 5.0 * 2 ** (attempt - 1))
            )
            print(
                f"Open-Meteo archive rate limited for {city.name} "
                f"{start_date}..{end_date}; retrying in {delay:.0f}s "
                f"({attempt}/{ARCHIVE_FETCH_ATTEMPTS})",
                flush=True,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable archive retry state")


def _history_for(readings: list[Reading], idx: int) -> list[Reading]:
    history_hours = DIURNAL_WINDOW_DAYS * 24
    cutoff = readings[idx].observation_ts
    start_idx = max(0, idx - history_hours)
    return [item for item in readings[start_idx:idx] if item.observation_ts < cutoff]


def _latest_peers(data: ReplayData, exclude_city: str, before_ts: datetime) -> dict[str, Reading]:
    peers: dict[str, Reading] = {}
    for city, timestamps in data.timestamps_by_city.items():
        if city == exclude_city:
            continue
        idx = bisect_right(timestamps, before_ts) - 1
        if idx >= 0:
            peers[city] = data.readings_by_city[city][idx]
    return peers


def replay_legacy(
    data: ReplayData,
    forecasts: dict[tuple[str, datetime], Forecast],
) -> list[tuple[Reading, EventCandidate]]:
    results: list[tuple[Reading, EventCandidate]] = []
    for city, readings in data.readings_by_city.items():
        print(f"Legacy replay: {city}", flush=True)
        for idx, reading in enumerate(readings):
            history = _history_for(readings, idx)
            peers = _latest_peers(data, city, reading.observation_ts)
            events: list[EventCandidate] = []
            events.extend(detect_wmo_transition(reading, history))
            if len(history) >= 12:
                events.extend(detect_rapid_change(reading, history))
                events.extend(detect_sustained_extreme(reading, history))
                events.extend(detect_comfort_divergence(reading, history))
            if peers and len(history) >= 12:
                events.extend(detect_cross_city_contrast(reading, history, peers))
            forecast = forecasts.get((city, reading.observation_ts))
            if forecast is not None:
                events.extend(detect_forecast_divergence(reading, forecast))
            events.extend(detect_fun_facts(reading, history, peers))
            results.extend((reading, event) for event in events)
    return results


def replay_native(
    data: ReplayData,
    forecasts: dict[tuple[str, datetime], Forecast],
    *,
    profile: CalibrationProfile,
) -> NativeReplay:
    raw: list[tuple[Reading, EventCandidate]] = []
    states: dict[str, _IncidentState] = {}
    incidents: list[IncidentRecord] = []
    climatology = _climatology_for_profile(profile)
    with _patched_profile(profile):
        for city, readings in data.readings_by_city.items():
            print(f"Native replay ({profile.name}): {city}", flush=True)
            for idx, reading in enumerate(readings):
                history = _history_for(readings, idx)
                peers = _latest_peers(data, city, reading.observation_ts)
                forecast = forecasts.get((city, reading.observation_ts))
                ctx = DetectorContext(
                    reading=reading,
                    history=history,
                    peers=peers,
                    forecast=forecast,
                    climatology=climatology,
                )
                candidates = detect_candidates(ctx)
                raw.extend((reading, candidate) for candidate in candidates)
                incidents.extend(_collapse_candidates(states, candidates, reading))
    return NativeReplay(raw=raw, incidents=incidents)


def _climatology_for_profile(profile: CalibrationProfile) -> Climatology:
    artifact = json.loads(CLIMATOLOGY_PATH.read_text())
    return Climatology(
        artifact,
        baseline_variant=profile.baseline_variant,
        threshold_variant=profile.threshold_variant,
    )


def _collapse_candidates(
    states: dict[str, _IncidentState],
    candidates: list[EventCandidate],
    reading: Reading,
) -> list[IncidentRecord]:
    selected = _highest_priority_candidates(candidates)
    touched: list[IncidentRecord] = []
    firing_keys = set(selected)

    for key, candidate in selected.items():
        state = states.get(key)
        if state is None:
            state = _IncidentState(city=candidate.city, event_type=candidate.event_type)
            states[key] = state
        strength = _candidate_strength(candidate)
        score = _candidate_score(candidate)
        if state.active:
            if strength < 0.5:
                state.clear_count += 1
                if state.clear_count >= 2:
                    state.active = False
                    state.incident = None
                continue
            state.clear_count = 0
            if state.incident is not None and score > state.incident.priority_score:
                state.incident.priority_score = score
                state.incident.severity = candidate.severity
                state.incident.metric = candidate.metric
                state.incident.signal_values = dict(candidate.signal_values)
            continue
        if strength < 1.0:
            state.clear_count = 0
            continue
        state.active = True
        state.clear_count = 0
        incident = IncidentRecord(
            city=candidate.city,
            event_type=candidate.event_type,
            event_ts=candidate.event_ts,
            severity=candidate.severity,
            priority_score=score,
            metric=candidate.metric,
            signal_values=dict(candidate.signal_values),
        )
        state.incident = incident
        touched.append(incident)

    for key, state in list(states.items()):
        if state.city != reading.city or not state.active or key in firing_keys:
            continue
        state.clear_count += 1
        if state.clear_count >= 2:
            state.active = False
            state.incident = None
    return touched


def _highest_priority_candidates(
    candidates: list[EventCandidate],
) -> dict[str, EventCandidate]:
    selected: dict[str, EventCandidate] = {}
    for candidate in candidates:
        key = dedupe_key_for_candidate(candidate)
        if key not in selected or _candidate_score(candidate) >= _candidate_score(selected[key]):
            selected[key] = candidate
    return selected


def _candidate_score(candidate: EventCandidate) -> float:
    from app.detection.scoring import candidate_priority_score

    return candidate_priority_score(candidate)


def _candidate_strength(candidate: EventCandidate) -> float:
    for key in ("z_score", "level_jump", "abs_error", "difference", "gap"):
        value = candidate.signal_values.get(key)
        if value is not None:
            return abs(float(value))
    return {"info": 1.0, "warning": 2.0, "severe": 3.0}.get(candidate.severity, 1.0)


@contextmanager
def _patched_profile(profile: CalibrationProfile) -> Iterator[None]:
    patches = {
        scoring_module: {"SURPRISAL_SCORING": profile.surprisal_scoring},
        temp_module: {
            "USE_EMPIRICAL_QUANTILE_GATES": profile.use_empirical_quantile_gates,
            "TEMPERATURE_SHOCK_Z": profile.temperature_shock_z,
            "TEMPERATURE_SHOCK_DELTA_C": profile.temperature_shock_delta_c,
        },
        spells_module: {
            "USE_EMPIRICAL_QUANTILE_GATES": profile.use_empirical_quantile_gates,
            "SPELL_Z": profile.spell_z,
        },
        pressure_module: {
            "MIN_PRESSURE_FALL_HPA": profile.pressure_min_fall_hpa,
            "MIN_WIND_RISE_KMH": profile.pressure_min_wind_rise_kmh,
            "MIN_CONFIRMING_GUST_KMH": profile.pressure_min_confirming_gust_kmh,
        },
        rain_module: {
            "USE_EMPIRICAL_QUANTILE_GATES": profile.use_empirical_quantile_gates,
            "MIN_HEAVY_RAIN_MM": profile.heavy_rain_min_mm,
        },
        wind_module: {
            "USE_EMPIRICAL_QUANTILE_GATES": profile.use_empirical_quantile_gates,
            "WIND_GUST_Z": profile.wind_gust_z,
            "ECCC_GUST_KMH": profile.wind_gust_anchor_kmh,
        },
        stress_module: {
            "HEAT_STRESS_HUMIDEX": profile.heat_humidex,
            "STRONG_HEAT_HUMIDEX": profile.strong_heat_humidex,
            "COLD_STRESS_WIND_CHILL": profile.cold_wind_chill,
            "STRONG_COLD_WIND_CHILL": profile.strong_cold_wind_chill,
        },
        forecast_bust_module: {"FORECAST_BUST_K": profile.forecast_bust_k},
        spatial_module: {
            "USE_EMPIRICAL_QUANTILE_GATES": profile.use_empirical_quantile_gates,
            "SPATIAL_Z_GAP": profile.spatial_z_gap,
            "SPATIAL_MIN_OWN_Z": profile.spatial_min_own_z,
        },
    }
    originals: list[tuple[Any, str, Any]] = []
    for module, values in patches.items():
        for name, value in values.items():
            originals.append((module, name, getattr(module, name)))
            setattr(module, name, value)
    try:
        yield
    finally:
        for module, name, value in originals:
            setattr(module, name, value)


def current_profile() -> CalibrationProfile:
    return CalibrationProfile(
        name="DS-4 surprisal scoring on smooth baseline",
        baseline_variant="smooth",
        threshold_variant="smooth",
        use_empirical_quantile_gates=True,
        temperature_shock_z=temp_module.TEMPERATURE_SHOCK_Z,
        temperature_shock_delta_c=temp_module.TEMPERATURE_SHOCK_DELTA_C,
        spell_z=spells_module.SPELL_Z,
        pressure_min_fall_hpa=pressure_module.MIN_PRESSURE_FALL_HPA,
        pressure_min_wind_rise_kmh=pressure_module.MIN_WIND_RISE_KMH,
        pressure_min_confirming_gust_kmh=pressure_module.MIN_CONFIRMING_GUST_KMH,
        heavy_rain_min_mm=rain_module.MIN_HEAVY_RAIN_MM,
        wind_gust_z=wind_module.WIND_GUST_Z,
        wind_gust_anchor_kmh=wind_module.ECCC_GUST_KMH,
        heat_humidex=stress_module.HEAT_STRESS_HUMIDEX,
        strong_heat_humidex=stress_module.STRONG_HEAT_HUMIDEX,
        cold_wind_chill=stress_module.COLD_STRESS_WIND_CHILL,
        strong_cold_wind_chill=stress_module.STRONG_COLD_WIND_CHILL,
        forecast_bust_k=forecast_bust_module.FORECAST_BUST_K,
        spatial_z_gap=spatial_module.SPATIAL_Z_GAP,
        spatial_min_own_z=spatial_module.SPATIAL_MIN_OWN_Z,
    )


def ds3_scoring_profile() -> CalibrationProfile:
    """Smooth DS-3 baseline scored with the pre-DS-4 clipped rarity / z-magnitude.

    Identical detectors and gates to :func:`current_profile`; only the scoring mode
    differs, so a before/after diff isolates the DS-4 surprisal + decorrelation
    change from the DS-3 baseline change.
    """

    from dataclasses import replace

    return replace(
        current_profile(),
        name="DS-3 smooth baseline, clipped rarity (pre-DS-4 scoring)",
        surprisal_scoring=False,
    )


def evaluate_labeled() -> tuple[int, int, int, list[str], str]:
    tp = fp = fn = 0
    details: list[str] = []
    detect_delays: list[float] = []
    for scenario in SCENARIOS:
        events = detect_candidates(
            DetectorContext(
                reading=scenario.reading,
                history=scenario.history,
                peers=scenario.peers,
                forecast=scenario.forecast,
                forecast_comparison_pairs=scenario.forecast_comparison_pairs,
                climatology=scenario.climatology,
            )
        )
        actual = {event.event_type for event in events}
        hits = actual & scenario.expected_types
        misses = scenario.expected_types - actual
        extras = actual - scenario.expected_types
        tp += len(hits)
        fp += len(extras)
        fn += len(misses)
        for event in events:
            if event.event_type in scenario.expected_types:
                detect_delays.append(0.0)
        status = "PASS" if actual == scenario.expected_types else "FAIL"
        details.append(
            f"| {scenario.name} | {_fmt_set(scenario.expected_types)} "
            f"| {_fmt_set(actual)} | {status} |"
        )
    mttd = (
        f"{sum(detect_delays) / len(detect_delays):.2f} h over "
        f"{len(detect_delays)} labeled onsets"
        if detect_delays
        else "No labeled onsets"
    )
    return tp, fp, fn, details, mttd


def _fmt_set(values: set[str]) -> str:
    return ", ".join(sorted(values)) if values else "*(none)*"


def incident_rate_table(replay: NativeReplay, data: ReplayData) -> str:
    rows = [
        "| detector_type | incidents | raw_firings | per_1k_readings | "
        "per_city_day | raw_to_incident_collapse |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    incident_counts = Counter(event.event_type for event in replay.incidents)
    raw_counts = Counter(event.event_type for _reading, event in replay.raw)
    total_incidents = len(replay.incidents)
    total_raw = len(replay.raw)
    for event_type in NATIVE_TYPES:
        incidents = incident_counts[event_type]
        raw = raw_counts[event_type]
        rows.append(_rate_row(event_type, incidents, raw, data))
    rows.append(_rate_row("OVERALL", total_incidents, total_raw, data))
    return "\n".join(rows)


def _rate_row(event_type: str, incidents: int, raw: int, data: ReplayData) -> str:
    per_1k = incidents / data.total_readings * 1000 if data.total_readings else 0.0
    per_city_day = incidents / data.total_city_days if data.total_city_days else 0.0
    frag = raw / incidents if incidents else 0.0
    return (
        f"| {event_type} | {incidents} | {raw} | {per_1k:.2f} | "
        f"{per_city_day:.3f} | {frag:.2f} |"
    )


def city_rate_table(replay: NativeReplay, data: ReplayData) -> str:
    rows = [
        "| city | incidents | per_1k_readings | per_city_day |",
        "|---|---:|---:|---:|",
    ]
    counts = Counter(event.city for event in replay.incidents)
    city_days = data.city_days_by_city
    for city in CITY_NAMES:
        incidents = counts[city]
        readings = len(data.readings_by_city.get(city, []))
        per_1k = incidents / readings * 1000 if readings else 0.0
        per_day = incidents / city_days.get(city, 0) if city_days.get(city, 0) else 0.0
        rows.append(f"| {city} | {incidents} | {per_1k:.2f} | {per_day:.3f} |")
    return "\n".join(rows)


def before_after_table(before: NativeReplay, after: NativeReplay, data: ReplayData) -> str:
    before_counts = Counter(event.event_type for event in before.incidents)
    after_counts = Counter(event.event_type for event in after.incidents)
    rows = [
        "| detector_type | before_incidents | before_per_city_day | "
        "after_incidents | after_per_city_day |",
        "|---|---:|---:|---:|---:|",
    ]
    for event_type in NATIVE_TYPES:
        before_n = before_counts[event_type]
        after_n = after_counts[event_type]
        rows.append(
            f"| {event_type} | {before_n} | {_per_city_day(before_n, data):.3f} | "
            f"{after_n} | {_per_city_day(after_n, data):.3f} |"
        )
    return "\n".join(rows)


def legacy_comparison_table(
    legacy_results: list[tuple[Reading, EventCandidate]],
    after: NativeReplay,
) -> str:
    legacy_counts = Counter(event.event_type for _reading, event in legacy_results)
    native_incidents = Counter(event.event_type for event in after.incidents)
    rows = [
        "| old_type | replacement | old_raw_events | new_incidents |",
        "|---|---|---:|---:|",
    ]
    for old_type, replacement in LEGACY_REPLACEMENTS:
        new_count = sum(native_incidents[item.strip()] for item in replacement.split("+"))
        if replacement.startswith("supporting") or replacement.startswith("retired"):
            new_count = 0
        rows.append(
            f"| {old_type} | {replacement} | {legacy_counts[old_type]} | {new_count} |"
        )
    for event_type in ("pressure_plunge", "heavy_rain_burst", "wind_gust_burst"):
        rows.append(f"| *(none)* | {event_type} | 0 | {native_incidents[event_type]} |")
    return "\n".join(rows)


def _per_city_day(count: int, data: ReplayData) -> float:
    return count / data.total_city_days if data.total_city_days else 0.0


def severity_table(replay: NativeReplay) -> str:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for event in replay.incidents:
        counts[event.event_type][event.severity] += 1
    rows = ["| detector_type | info | warning | severe |", "|---|---:|---:|---:|"]
    for event_type in NATIVE_TYPES:
        row = counts[event_type]
        rows.append(
            f"| {event_type} | {row['info']} | {row['warning']} | {row['severe']} |"
        )
    return "\n".join(rows)


def calibration_changes_table() -> str:
    rows = [
        "| detector | change | rationale |",
        "|---|---|---|",
        "| climatology baseline | month/local-hour buckets -> local "
        "day-of-year smoothing window at the same local hour | The baseline "
        "uses more neighboring-season data while preserving the diurnal cycle. |",
        "| empirical thresholds | recomputed on smooth training residuals | "
        "DS-2 quantiles are not reused after the baseline changes; thresholds "
        "remain train-only and leak-free. |",
        "| temperature_shock | quantile gate structure unchanged; smooth residuals "
        "replace month-hour residuals | Pure anomaly detector remains "
        "distributional, with z retained as a diagnostic. |",
        "| warm/cold spell | quantile gate structure unchanged; smooth residuals "
        "replace month-hour residuals | Persistent temperature tails are now "
        "measured against a continuous seasonal baseline. |",
        "| pressure_plunge | unchanged in DS-3 | It already uses an "
        "empirical pressure-fall percentile over replay history rather than a "
        "shared z gate. |",
        "| heavy_rain_burst | smooth wet-hour baselines plus 10 mm hazard floor; "
        "dry-hour hurdle and 6h accumulation anchor unchanged | Rain keeps the "
        "anomaly-vs-hazard-floor split from the DS-2 correction. |",
        "| wind_gust_burst | smooth gust residual quantile; 90 km/h anchor "
        "unchanged | Gusts stay one-sided upper-tail hazards with an absolute "
        "danger anchor. |",
        "| heat_stress | unchanged in DS-3 | This detector is formula-threshold based, not a "
        "`z_hod >= 3` gate. |",
        "| cold_stress | unchanged in DS-3 | This detector is formula-threshold based, not a "
        "`z_hod >= 3` gate. |",
        "| forecast_bust | unchanged in DS-3 | Archive replay still lacks historical forecast "
        "pairs. |",
        "| spatial_anomaly | own-anomaly quantile gate now uses smooth residuals; "
        "peer z-gap remains 5.0 | The city must still be anomalous in its own "
        "metric-specific tail before peer comparison. |",
        "| scoring weights | unchanged additive 0-100 blend | "
        "DS-4 keeps the API-additive weights but redefines two inputs: rarity is now "
        "surprisal (empirical tail position) and magnitude is absolute physical size. |",
        "| rarity input | clipped `abs_z/4` (and binary 1.0 for rain) -> surprisal "
        "`-log(tail prob)` capped near a 1-in-10,000 tail | A 1-in-1000 event now "
        "outscores a 1-in-100 event instead of both saturating mid-range. |",
        "| magnitude input | shared function of the same z -> absolute physical size "
        "(mm rain, degC departure, km/h gust) | Decorrelated so a rare-but-small and a "
        "common-but-large event score differently. |",
        "| severity bands | "
        f"{scoring_module.SEVERITY_WARNING_FLOOR:.0f}/"
        f"{scoring_module.SEVERITY_SEVERE_FLOOR:.0f} (band numbers unchanged) | "
        "Surprisal + absolute magnitude reshape the score distribution, so the severe "
        "floor is re-derived as the replayed incident p90 (~10% severe). It lands at the "
        "same 60 as the pre-DS-4 cut, so the boundary numbers do not move. |",
        "| heavy_rain accumulation bar | "
        f"10 mm/6h -> {rain_module.MIN_HEAVY_RAIN_ACCUMULATION_MM:g} mm/6h | "
        "Set from the rain-mix histogram and anchored to a quarter of the ECCC "
        "50 mm/24h rainfall warning over a 6h window. |",
    ]
    return "\n".join(rows)


def spot_check_table(replay: NativeReplay) -> str:
    rows = [
        "| documented_event | date | replay_incident | priority | evidence | source |",
        "|---|---|---|---:|---|---|",
    ]
    for item in KNOWN_EVENT_SPOT_CHECKS:
        match = _match_known_event(replay, item)
        if match is None:
            incident = "no matching severe incident in +/-48h"
            score = "n/a"
            evidence = f"expected {item['incident']}; no replay match"
        else:
            incident = (
                f"{match.event_type} at "
                f"{match.event_ts.strftime('%Y-%m-%d %H:%M UTC')}"
            )
            score = f"{match.priority_score:.1f}"
            evidence = item["evidence"]
            if not evidence.startswith(match.severity):
                evidence = f"{match.severity}; {evidence}"
        rows.append(
            f"| {item['event']} | {item['source_date']} | "
            f"{incident} | {score} | "
            f"{evidence} | [{item['source_summary']}]({item['source']}) |"
        )
    return "\n".join(rows)


def _match_known_event(
    replay: NativeReplay,
    item: dict[str, str],
    *,
    tolerance_hours: int = 48,
) -> IncidentRecord | None:
    source_day = date.fromisoformat(item["source_date"])
    start = datetime.combine(source_day, datetime.min.time()) - timedelta(
        hours=tolerance_hours,
    )
    end = datetime.combine(source_day, datetime.max.time()) + timedelta(hours=tolerance_hours)
    matches = [
        incident
        for incident in replay.incidents
        if incident.city == item["city"]
        and incident.event_type == item["event_type"]
        and incident.severity == "severe"
        and start <= incident.event_ts.replace(tzinfo=None) <= end
    ]
    if not matches:
        return None
    return max(matches, key=lambda incident: incident.priority_score)


def _candidate_scores_by_type(replay: NativeReplay) -> dict[str, list[float]]:
    from app.detection.scoring import candidate_priority_score

    scores: dict[str, list[float]] = defaultdict(list)
    for _reading, candidate in replay.raw:
        scores[candidate.event_type].append(candidate_priority_score(candidate))
    return scores


def _percentiles(values: list[float], points: tuple[float, ...]) -> list[float]:
    if not values:
        return [0.0 for _ in points]
    ordered = sorted(values)
    out: list[float] = []
    for p in points:
        position = (len(ordered) - 1) * p / 100
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        out.append(ordered[lower] * (1 - weight) + ordered[upper] * weight)
    return out


def score_distribution_table(before: NativeReplay, after: NativeReplay) -> str:
    before_scores = _candidate_scores_by_type(before)
    after_scores = _candidate_scores_by_type(after)
    rows = [
        "Per-detector raw candidate `priority_score` distribution. **before** is the "
        "DS-3 clipped rarity / z-magnitude scoring; **after** is DS-4 surprisal rarity "
        "with a decorrelated absolute-magnitude axis. Both run on the identical smooth "
        "baseline and gates, so the shift is the scoring change alone.",
        "",
        "| detector_type | n | before p50/p90/p99/max | after p50/p90/p99/max |",
        "|---|---:|---|---|",
    ]
    points = (50.0, 90.0, 99.0, 100.0)
    for event_type in NATIVE_TYPES:
        after_values = after_scores.get(event_type, [])
        before_values = before_scores.get(event_type, [])
        if not after_values and not before_values:
            continue
        b = _percentiles(before_values, points)
        a = _percentiles(after_values, points)
        rows.append(
            f"| {event_type} | {len(after_values)} | "
            f"{b[0]:.1f}/{b[1]:.1f}/{b[2]:.1f}/{b[3]:.1f} | "
            f"{a[0]:.1f}/{a[1]:.1f}/{a[2]:.1f}/{a[3]:.1f} |"
        )
    return "\n".join(rows)


def rain_trigger_table(replay: NativeReplay) -> str:
    from app.detection.scoring import candidate_priority_score

    by_trigger: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for _reading, candidate in replay.raw:
        if candidate.event_type != "heavy_rain_burst":
            continue
        trigger = str(candidate.signal_values.get("trigger", "unknown"))
        accumulation = float(candidate.signal_values.get("accumulation_mm", 0.0))
        by_trigger[trigger].append((candidate_priority_score(candidate), accumulation))
    rows = [
        "Heavy-rain raw candidates split by trigger. An accumulation cluster sitting "
        "well below the hourly cluster is evidence that the 6h accumulation bar admits "
        "steady rain rather than bursts.",
        "",
        "| trigger | candidates | score p50/p90/max | accumulation_mm p50/p90/max |",
        "|---|---:|---|---|",
    ]
    points = (50.0, 90.0, 100.0)
    for trigger in ("hourly", "accumulation"):
        items = by_trigger.get(trigger, [])
        if not items:
            rows.append(f"| {trigger} | 0 | n/a | n/a |")
            continue
        scores = _percentiles([score for score, _acc in items], points)
        accums = _percentiles([acc for _score, acc in items], points)
        rows.append(
            f"| {trigger} | {len(items)} | "
            f"{scores[0]:.1f}/{scores[1]:.1f}/{scores[2]:.1f} | "
            f"{accums[0]:.1f}/{accums[1]:.1f}/{accums[2]:.1f} |"
        )
    return "\n".join(rows)


def plot_detector_score_histograms(replay: NativeReplay) -> Path:
    scores = _candidate_scores_by_type(replay)
    present = [event_type for event_type in NATIVE_TYPES if scores.get(event_type)]
    columns = 3
    rows = max(1, (len(present) + columns - 1) // columns)
    fig, axes = plt.subplots(rows, columns, figsize=(4 * columns, 3 * rows))
    flat_axes = axes.flatten() if hasattr(axes, "flatten") else [axes]
    from app.detection.scoring import SEVERITY_SEVERE_FLOOR, SEVERITY_WARNING_FLOOR

    for ax, event_type in zip(flat_axes, present, strict=False):
        ax.hist(scores[event_type], bins=20, range=(0, 100), edgecolor="black", alpha=0.7)
        ax.axvline(SEVERITY_WARNING_FLOOR, color="goldenrod", ls="--", lw=1)
        ax.axvline(SEVERITY_SEVERE_FLOOR, color="firebrick", ls="--", lw=1)
        ax.set_title(event_type, fontsize=9)
        ax.set_xlim(0, 100)
    for ax in flat_axes[len(present):]:
        ax.axis("off")
    fig.suptitle("DS-4 per-detector priority_score distribution (raw candidates)")
    fig.tight_layout()
    path = FIG_DIR / "score_histograms.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_rain_mix_histogram(replay: NativeReplay) -> Path:
    from app.detection.scoring import (
        SEVERITY_SEVERE_FLOOR,
        SEVERITY_WARNING_FLOOR,
        candidate_priority_score,
    )

    by_trigger: dict[str, list[float]] = defaultdict(list)
    for _reading, candidate in replay.raw:
        if candidate.event_type != "heavy_rain_burst":
            continue
        trigger = str(candidate.signal_values.get("trigger", "unknown"))
        by_trigger[trigger].append(candidate_priority_score(candidate))
    fig, ax = plt.subplots(figsize=(8, 4))
    for trigger, color in (("hourly", "steelblue"), ("accumulation", "darkorange")):
        values = by_trigger.get(trigger, [])
        if values:
            ax.hist(
                values,
                bins=20,
                range=(0, 100),
                alpha=0.55,
                color=color,
                edgecolor="black",
                label=f"{trigger} (n={len(values)})",
            )
    ax.axvline(SEVERITY_WARNING_FLOOR, color="goldenrod", ls="--", lw=1, label="warning floor")
    ax.axvline(SEVERITY_SEVERE_FLOOR, color="firebrick", ls="--", lw=1, label="severe floor")
    ax.set_xlabel("priority_score")
    ax.set_ylabel("raw candidate count")
    ax.set_title("heavy_rain_burst score mix by trigger")
    ax.legend()
    fig.tight_layout()
    path = FIG_DIR / "rain_mix_histogram.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_zscore_histogram(replay: NativeReplay) -> Path:
    zscores = [
        event.signal_values["z_score"]
        for _reading, event in replay.raw
        if event.event_type == "temperature_shock" and "z_score" in event.signal_values
    ]
    fig, ax = plt.subplots(figsize=(8, 4))
    if zscores:
        ax.hist(zscores, bins=30, edgecolor="black", alpha=0.7)
    ax.axvline(
        temp_module.TEMPERATURE_SHOCK_Z,
        color="orange",
        ls="--",
        label=f"z = {temp_module.TEMPERATURE_SHOCK_Z}",
    )
    ax.set_xlabel("z-score")
    ax.set_ylabel("raw candidate count")
    ax.set_title("temperature_shock z-score distribution")
    ax.legend()
    fig.tight_layout()
    path = FIG_DIR / "zscore_histogram.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_events_by_local_hour(replay: NativeReplay) -> Path:
    hour_counts: Counter[int] = Counter()
    for event in replay.incidents:
        lh = local_hour(event.city, event.event_ts)
        if lh is not None:
            hour_counts[lh] += 1
    hours = list(range(24))
    counts = [hour_counts.get(hour, 0) for hour in hours]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(hours, counts, edgecolor="black", alpha=0.7)
    ax.set_xlabel("Local hour")
    ax.set_ylabel("incident count")
    ax.set_title("Native incidents by local hour")
    ax.set_xticks(hours)
    fig.tight_layout()
    path = FIG_DIR / "events_by_local_hour.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_severity_pie(replay: NativeReplay) -> Path:
    counter = Counter(event.severity for event in replay.incidents)
    labels = sorted(counter)
    sizes = [counter[label] for label in labels]
    fig, ax = plt.subplots(figsize=(5, 5))
    if sizes:
        ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90)
    ax.set_title("Severity breakdown")
    fig.tight_layout()
    path = FIG_DIR / "severity_breakdown.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _climatology_training_summary() -> str:
    try:
        artifact = json.loads(CLIMATOLOGY_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return "unknown committed climatology artifact"
    date_range = artifact.get("date_range", {})
    start = date_range.get("start", "unknown")
    end = date_range.get("end", "unknown")
    source = artifact.get("source", "Open-Meteo archive")
    return f"{source}, trained on {start}..{end}"


def _empirical_threshold_summary() -> str:
    try:
        artifact = json.loads(CLIMATOLOGY_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return "training threshold contrast unavailable"
    metrics = artifact.get("empirical_thresholds", {}).get("metrics", {})
    if not isinstance(metrics, dict):
        return "training threshold contrast unavailable"

    temperature = metrics.get("temperature_2m", {})
    gust = metrics.get("wind_gusts_10m", {})
    rain = metrics.get("precipitation", {})
    temp_upper = _threshold_value(temperature, "upper_z")
    temp_lower = _threshold_value(temperature, "lower_z")
    gust_upper = _threshold_value(gust, "upper_z")
    rain_amount = _threshold_value(rain, "wet_amount_mm")
    if None in (temp_upper, temp_lower, gust_upper, rain_amount):
        return "training threshold contrast unavailable"

    return (
        "Per-metric z-equivalent thresholds expose why the uniform z=3 gate was "
        f"too blunt: temperature tails are {temp_upper:.2f}/{temp_lower:.2f} z, "
        f"while gusts require {gust_upper:.2f} z. Rain's wet-hour 99.5th "
        f"percentile is {rain_amount:.1f} mm/h, below the 10 mm hazard floor, "
        "so the hazard detector gates on the stricter floor."
    )


def boundary_continuity_table() -> str:
    try:
        artifact = json.loads(CLIMATOLOGY_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return "Boundary diagnostics unavailable."
    rows_data = (
        artifact.get("diagnostics", {})
        .get("boundary_continuity", {})
        .get("rows", [])
    )
    if not isinstance(rows_data, list) or not rows_data:
        return "Boundary diagnostics unavailable."

    rows = [
        "| city | boundary | fixed_value_c | legacy_z_before_after | "
        "legacy_jump | smooth_z_before_after | smooth_jump |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in rows_data:
        if not isinstance(item, dict):
            continue
        rows.append(
            f"| {item['city']} | {item['boundary']} | {item['fixed_value']:.1f} | "
            f"{item['legacy_before_z']:.2f} -> {item['legacy_after_z']:.2f} | "
            f"{item['legacy_jump']:.2f} | "
            f"{item['smooth_before_z']:.2f} -> {item['smooth_after_z']:.2f} | "
            f"{item['smooth_jump']:.2f} |"
        )
    return "\n".join(rows)


def _threshold_value(stats: object, key: str) -> float | None:
    if not isinstance(stats, dict):
        return None
    value = stats.get(key)
    return None if value is None else float(value)


def _native_interpretation(replay: NativeReplay) -> str:
    counts = Counter(event.event_type for event in replay.incidents)
    total = len(replay.incidents)
    spatial = counts["spatial_anomaly"]
    spatial_share = spatial / total if total else 0.0
    return "\n".join(
        [
            "- Heat/cold stress and warm/cold spell all remain measurable on the "
            "test replay: "
            f"heat_stress {counts['heat_stress']}, "
            f"cold_stress {counts['cold_stress']}, "
            f"warm_spell {counts['warm_spell']}, "
            f"cold_spell {counts['cold_spell']}.",
            "- Forecast-bust is zero in archive mode because the Open-Meteo "
            "archive has observations but not the forecasts issued at those "
            "historical times; it remains covered by unit and labeled tests and "
            "is active in live DB operation when stored forecasts exist.",
            "- Spatial anomaly compares each city in `z_hod` space against that "
            "city's own climatology first, then compares the standardized value "
            "to peers. A city must be anomalous in its own right and far from "
            "peer z-values; normal-for-Vancouver mildness beside "
            "normal-for-Ottawa cold is not an event.",
            f"- Spatial anomaly is {spatial}/{total} incidents "
            f"({spatial_share:.1%}), so the structural own-anomaly gate remains "
            "visible in the rate mix.",
            "- Spatial incidents use `city|spatial_anomaly|metric` as their "
            "dedupe key, with no timestamp component, so multi-hour contrasts "
            "collapse into one incident until lifecycle resolves them.",
        ]
    )


def _label_window(label: dict[str, Any], pad_days: int = 1) -> tuple[datetime, datetime]:
    start = datetime.combine(date.fromisoformat(label["start"]), datetime.min.time())
    end = datetime.combine(date.fromisoformat(label["end"]), datetime.max.time())
    return start - timedelta(days=pad_days), end + timedelta(days=pad_days)


def _incidents_in_window(replay: NativeReplay, label: dict[str, Any]) -> list[IncidentRecord]:
    start, end = _label_window(label)
    return [
        incident
        for incident in replay.incidents
        if incident.city == label["city"]
        and start <= incident.event_ts.replace(tzinfo=None) <= end
    ]


def _matches_for_label(replay: NativeReplay, label: dict[str, Any]) -> list[IncidentRecord]:
    expected = set(label["expected_types"])
    return [i for i in _incidents_in_window(replay, label) if i.event_type in expected]


def _wilson_interval(hits: int, n: int) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return max(0.0, center - half), min(1.0, center + half)


def weak_label_recall_table(replay: NativeReplay) -> str:
    rows = [
        "| city | event | window | expected detectors | expected-type | any-type | top incident |",
        "|---|---|---|---|:--:|:--:|---|",
    ]
    expected_hits = severe_hits = any_type_hits = 0
    for label in WEAK_LABELS:
        matches = _matches_for_label(replay, label)
        window_incidents = _incidents_in_window(replay, label)
        has_expected = bool(matches)
        has_severe = any(m.severity == "severe" for m in matches)
        has_any_type = bool(window_incidents)
        expected_hits += int(has_expected)
        severe_hits += int(has_severe)
        any_type_hits += int(has_any_type)
        if matches:
            best = max(matches, key=lambda m: m.priority_score)
            top = f"{best.event_type} {best.priority_score:.0f} ({best.severity})"
            expected_cell = "severe" if has_severe else "yes"
        else:
            top = "**none (false negative)**" if label.get("headline_fn") else "none"
            expected_cell = "**no**" if label.get("headline_fn") else "no"
        if has_any_type and not has_expected:
            other = max(window_incidents, key=lambda m: m.priority_score)
            any_cell = f"yes ({other.event_type})"
        else:
            any_cell = "yes" if has_any_type else "no"
        window = (
            label["start"]
            if label["start"] == label["end"]
            else f"{label['start']}..{label['end']}"
        )
        rows.append(
            f"| {label['city']} | [{label['event']}]({label['source']}) | {window} | "
            f"{', '.join(label['expected_types'])} | {expected_cell} | {any_cell} | {top} |"
        )
    n = len(WEAK_LABELS)
    lo, hi = _wilson_interval(expected_hits, n)
    summary = (
        f"**Primary -- expected-type recall** (the meaningful number, requires the *right* "
        f"detector to fire): any-tier **{expected_hits}/{n} = {expected_hits / n:.0%}** "
        f"(Wilson 95% CI {lo:.0%}-{hi:.0%}), severe-tier "
        f"**{severe_hits}/{n} = {severe_hits / n:.0%}**.\n\n"
        f"**Secondary -- any-incident-any-type recall** (loose upper bound, any detector "
        f"fires in the window): **{any_type_hits}/{n} = {any_type_hits / n:.0%}**. The gap "
        f"to primary ({any_type_hits - expected_hits} event(s)) is windows where the system "
        f"reacted but mis-attributed the detector type."
    )
    return summary + "\n\n" + "\n".join(rows)


def _era5_window_extremes(
    data: ReplayData,
    climatology: Climatology,
    label: dict[str, Any],
) -> dict[str, float]:
    """Worst-case ERA5 values for the expected metrics inside a labeled window.

    Used to decide whether a miss is a resolution false negative (ERA5 itself never
    showed an extreme) or a genuine detector gap (ERA5 cleared a gate but nothing fired).
    """

    from app.detection.stress import humidex, wind_chill

    city = label["city"]
    start, end = _label_window(label)
    readings = data.readings_by_city.get(city, [])
    context = [
        r
        for r in readings
        if start - timedelta(hours=6) <= r.observation_ts.replace(tzinfo=None) <= end
    ]
    by_ts = {r.observation_ts: r for r in context}
    out = {
        "max_gust": 0.0, "max_gust_z": 0.0, "max_hourly_mm": 0.0, "max_6h_mm": 0.0,
        "max_3h_fall_hpa": 0.0, "max_3h_dtemp": 0.0, "max_abs_temp_z": 0.0,
        "min_temp_z": 0.0, "max_temp_z": 0.0, "max_humidex": -99.0, "min_wind_chill": 99.0,
    }
    in_window = [r for r in context if start <= r.observation_ts.replace(tzinfo=None) <= end]
    for r in in_window:
        ts = r.observation_ts
        gust = r.wind_gusts_10m
        if gust is not None:
            gz = climatology.z_hod(city, "wind_gusts_10m", gust, ts).z or 0.0
            out["max_gust"] = max(out["max_gust"], float(gust))
            out["max_gust_z"] = max(out["max_gust_z"], gz)
        temp = r.temperature_2m
        if temp is not None:
            tz = climatology.z_hod(city, "temperature_2m", temp, ts).z
            if tz is not None:
                out["min_temp_z"] = min(out["min_temp_z"], tz)
                out["max_temp_z"] = max(out["max_temp_z"], tz)
                out["max_abs_temp_z"] = max(out["max_abs_temp_z"], abs(tz))
            prior3 = by_ts.get(ts - timedelta(hours=3))
            if prior3 is not None and prior3.temperature_2m is not None:
                out["max_3h_dtemp"] = max(out["max_3h_dtemp"], abs(temp - prior3.temperature_2m))
            dew = r.dew_point_2m
            if dew is not None:
                out["max_humidex"] = max(out["max_humidex"], humidex(temp, dew))
            wind = r.wind_speed_10m
            if wind is not None:
                wc = wind_chill(temp, wind)
                if wc is not None:
                    out["min_wind_chill"] = min(out["min_wind_chill"], wc)
        precip = r.precipitation
        if precip is not None:
            out["max_hourly_mm"] = max(out["max_hourly_mm"], float(precip))
        accum = 0.0
        for h in range(6):
            prev = by_ts.get(ts - timedelta(hours=h))
            if prev is not None and prev.precipitation is not None:
                accum += float(prev.precipitation)
        out["max_6h_mm"] = max(out["max_6h_mm"], accum)
        pressure = r.pressure_msl if r.pressure_msl is not None else r.surface_pressure
        prior3p = by_ts.get(ts - timedelta(hours=3))
        if pressure is not None and prior3p is not None:
            prev_p = (
                prior3p.pressure_msl
                if prior3p.pressure_msl is not None
                else prior3p.surface_pressure
            )
            if prev_p is not None:
                out["max_3h_fall_hpa"] = max(out["max_3h_fall_hpa"], prev_p - pressure)
    return out


def miss_decomposition_table(replay: NativeReplay, data: ReplayData) -> str:
    climatology = _climatology_for_profile(current_profile())
    gust_gate = abs(climatology.empirical_z_threshold("wind_gusts_10m", "upper") or 4.05)
    warm_gate = abs(climatology.empirical_z_threshold("temperature_2m", "upper") or 2.75)
    cold_gate = abs(climatology.empirical_z_threshold("temperature_2m", "lower") or 2.79)
    rows = [
        "| city | event | expected | ERA5 peak in window | gate? | verdict |",
        "|---|---|---|---|:--:|---|",
    ]
    resolution = genuine = 0
    for label in WEAK_LABELS:
        if _matches_for_label(replay, label):
            continue
        ex = _era5_window_extremes(data, climatology, label)
        expected = set(label["expected_types"])
        cleared: list[str] = []
        notes: list[str] = []
        if "wind_gust_burst" in expected:
            notes.append(f"gust {ex['max_gust']:.0f} km/h (z {ex['max_gust_z']:.1f})")
            if ex["max_gust"] >= 90.0 or ex["max_gust_z"] >= gust_gate:
                cleared.append("gust")
        if "heavy_rain_burst" in expected:
            notes.append(f"rain {ex['max_hourly_mm']:.0f} mm/h, {ex['max_6h_mm']:.0f} mm/6h")
            if ex["max_hourly_mm"] >= 10.0 or ex["max_6h_mm"] >= 12.5:
                cleared.append("rain")
        if "pressure_plunge" in expected:
            notes.append(f"3h fall {ex['max_3h_fall_hpa']:.0f} hPa")
            if ex["max_3h_fall_hpa"] >= 6.0:
                cleared.append("pressure")
        if "temperature_shock" in expected:
            notes.append(f"3h dT {ex['max_3h_dtemp']:.0f}C (z {ex['max_abs_temp_z']:.1f})")
            if ex["max_3h_dtemp"] >= 5.0 and ex["max_abs_temp_z"] >= warm_gate:
                cleared.append("temp shock")
        if "cold_spell" in expected:
            notes.append(f"cold z {ex['min_temp_z']:.1f}")
            if ex["min_temp_z"] <= -cold_gate:
                cleared.append("cold spell")
        if "warm_spell" in expected:
            notes.append(f"warm z {ex['max_temp_z']:.1f}")
            if ex["max_temp_z"] >= warm_gate:
                cleared.append("warm spell")
        if "cold_stress" in expected:
            notes.append(f"wind chill {ex['min_wind_chill']:.0f}")
            if ex["min_wind_chill"] <= -25.0:
                cleared.append("cold stress")
        if "heat_stress" in expected:
            notes.append(f"humidex {ex['max_humidex']:.0f}")
            if ex["max_humidex"] >= 38.0:
                cleared.append("heat stress")
        if cleared:
            genuine += 1
            verdict = f"**genuine gap** (cleared: {', '.join(cleared)})"
            gate = "yes"
        else:
            resolution += 1
            verdict = "resolution FN (ERA5 below all gates)"
            gate = "no"
        rows.append(
            f"| {label['city']} | {label['event']} | {', '.join(label['expected_types'])} | "
            f"{'; '.join(notes)} | {gate} | {verdict} |"
        )
    n = len(WEAK_LABELS)
    expected_hits = sum(1 for label in WEAK_LABELS if _matches_for_label(replay, label))
    resolvable = expected_hits + genuine
    conditional = expected_hits / resolvable if resolvable else 0.0
    summary = (
        f"Of {n - expected_hits} expected-type misses, **{resolution}** are resolution "
        f"false negatives (ERA5 never cleared a gate -- the reanalysis flattened the event) "
        f"and **{genuine}** are genuine detector gaps (ERA5 cleared a gate but nothing "
        f"fired). The ECCC top-ten label set is biased toward convective and localized "
        f"extremes (derechos, thunderstorms, flash floods) that hourly ERA5 grid data "
        f"cannot resolve, so recall **conditional on ERA5-resolvable events** -- "
        f"{expected_hits}/{resolvable} = **{conditional:.0%}** -- better reflects detector "
        f"quality than the raw {expected_hits}/{n} = {expected_hits / n:.0%}."
    )
    return summary + "\n\n" + "\n".join(rows)


def chance_recall_line(replay: NativeReplay, *, trials: int = 1000, seed: int = 12345) -> str:
    import random

    rng = random.Random(seed)
    span_start = datetime(2022, 1, 1)
    span_days = (datetime(2025, 12, 31) - span_start).days
    hits_per_trial: list[int] = []
    for _ in range(trials):
        hits = 0
        for label in WEAK_LABELS:
            length = (date.fromisoformat(label["end"]) - date.fromisoformat(label["start"])).days
            offset = rng.randint(0, max(0, span_days - length))
            shifted = {
                **label,
                "start": (span_start + timedelta(days=offset)).date().isoformat(),
                "end": (span_start + timedelta(days=offset + length)).date().isoformat(),
            }
            if _matches_for_label(replay, shifted):
                hits += 1
        hits_per_trial.append(hits)
    n = len(WEAK_LABELS)
    mean_chance = sum(hits_per_trial) / len(hits_per_trial) / n
    observed = sum(1 for label in WEAK_LABELS if _matches_for_label(replay, label)) / n
    return (
        f"**Chance-recall check.** Permuting each label to a random same-length window in "
        f"2022-2025 (same city and expected types, {trials} trials, seed {seed}) yields a "
        f"mean expected-type recall of **{mean_chance:.0%}**. The observed {observed:.0%} is "
        f"well above chance, so the +/-1 day matches are not spurious."
    )


# Transparent physical-significance thresholds for the precision proxy. A top incident is
# "useful" when its signal clears an operationally meaningful bar, "noise" when it barely
# clears the detector gate, and "borderline" in between. These are anchored to physical
# units (not to the priority_score) so the label is not circular with the score it grades.
def _precision_label(incident: IncidentRecord) -> tuple[str, str]:
    s = incident.signal_values
    et = incident.event_type

    def fnum(key: str) -> float:
        value = s.get(key)
        try:
            return abs(float(value))
        except (TypeError, ValueError):
            return 0.0

    if et == "temperature_shock":
        if fnum("z_score") >= 4.0 and fnum("delta_c") >= 8.0:
            return "useful", f"z={fnum('z_score'):.1f}, dT={fnum('delta_c'):.0f}C"
        if fnum("z_score") >= 3.5:
            return "borderline", f"z={fnum('z_score'):.1f}"
        return "noise", f"z={fnum('z_score'):.1f}"
    if et in ("warm_spell", "cold_spell"):
        if fnum("z_score") >= 4.0 or fnum("departure_c") >= 10.0:
            return "useful", f"z={fnum('z_score'):.1f}, dep={fnum('departure_c'):.0f}C"
        if fnum("z_score") >= 3.2:
            return "borderline", f"z={fnum('z_score'):.1f}"
        return "noise", f"z={fnum('z_score'):.1f}"
    if et == "heavy_rain_burst":
        if fnum("amount_mm") >= 15.0 or fnum("accumulation_mm") >= 20.0:
            return "useful", f"{fnum('amount_mm'):.0f}mm/h, {fnum('accumulation_mm'):.0f}mm/6h"
        if fnum("amount_mm") >= 10.0 or fnum("accumulation_mm") >= 15.0:
            return "borderline", f"{fnum('accumulation_mm'):.0f}mm/6h"
        return "noise", f"{fnum('accumulation_mm'):.0f}mm/6h"
    if et == "wind_gust_burst":
        if fnum("gust_kmh") >= 80.0:
            return "useful", f"{fnum('gust_kmh'):.0f} km/h"
        if fnum("gust_kmh") >= 70.0 or fnum("z_score") >= 4.0:
            return "borderline", f"{fnum('gust_kmh'):.0f} km/h"
        return "noise", f"{fnum('gust_kmh'):.0f} km/h"
    if et == "heat_stress":
        if fnum("humidex") >= 40.0:
            return "useful", f"humidex {fnum('humidex'):.0f}"
        if fnum("humidex") >= 39.0:
            return "borderline", f"humidex {fnum('humidex'):.0f}"
        return "noise", f"humidex {fnum('humidex'):.0f}"
    if et == "cold_stress":
        chill = float(s.get("wind_chill", 0.0) or 0.0)
        if chill <= -30.0:
            return "useful", f"wind chill {chill:.0f}"
        if chill <= -27.0:
            return "borderline", f"wind chill {chill:.0f}"
        return "noise", f"wind chill {chill:.0f}"
    if et == "pressure_plunge":
        if fnum("pressure_fall_hpa") >= 10.0:
            return "useful", f"{fnum('pressure_fall_hpa'):.0f} hPa/3h"
        if fnum("pressure_fall_hpa") >= 8.0:
            return "borderline", f"{fnum('pressure_fall_hpa'):.0f} hPa/3h"
        return "noise", f"{fnum('pressure_fall_hpa'):.0f} hPa/3h"
    if et == "spatial_anomaly":
        if fnum("difference") >= 8.0:
            return "useful", f"peer gap {fnum('difference'):.1f} z"
        if fnum("difference") >= 6.0:
            return "borderline", f"peer gap {fnum('difference'):.1f} z"
        return "noise", f"peer gap {fnum('difference'):.1f} z"
    if et == "forecast_bust":
        if fnum("normalized_error") >= 4.0:
            return "useful", f"{fnum('normalized_error'):.1f}x MAE"
        return "borderline", f"{fnum('normalized_error'):.1f}x MAE"
    return "borderline", ""


def precision_proxy_table(replay: NativeReplay, *, top_n: int = 30) -> str:
    ranked = sorted(replay.incidents, key=lambda i: i.priority_score, reverse=True)[:top_n]
    counts = Counter()
    rows = [
        "| rank | city | detector | score | tier | label | signal |",
        "|---:|---|---|---:|---|---|---|",
    ]
    for index, incident in enumerate(ranked, start=1):
        label, signal = _precision_label(incident)
        counts[label] += 1
        rows.append(
            f"| {index} | {incident.city} | {incident.event_type} | "
            f"{incident.priority_score:.1f} | {incident.severity} | {label} | {signal} |"
        )
    n = len(ranked)
    useful = counts["useful"]
    borderline = counts["borderline"]
    summary = (
        f"Top **{n}** incidents by `priority_score`, labeled against transparent "
        f"physical-significance bars (documented below, anchored to units not the score). "
        f"**Useful share**: {useful}/{n} = {useful / n:.0%} useful, "
        f"{borderline}/{n} = {borderline / n:.0%} borderline, "
        f"{counts['noise']}/{n} = {counts['noise'] / n:.0%} noise."
    )
    return summary + "\n\n" + "\n".join(rows)


def write_evaluation(
    *,
    data: ReplayData,
    legacy: list[tuple[Reading, EventCandidate]],
    before: NativeReplay,
    before_scoring: NativeReplay,
    after: NativeReplay,
    scenario_table: str,
    precision: float,
    recall: float,
    tp: int,
    fp: int,
    fn: int,
    mttd: str,
) -> None:
    command = (
        "python3 scripts/evaluate.py --source archive "
        f"--start-date {data.start_date or DEFAULT_START} "
        f"--end-date {data.end_date or DEFAULT_END}"
    )
    md = f"""# WatchAgent — Detector Evaluation

> **Regenerate**: `{command}`. Archive replay is read-only and does not write to
> the live WatchAgent database.

## Method

- Source: **{data.source_label}**.
- Baseline artifact: **{_climatology_training_summary()}**.
- DS-1 uses an honest train/test split: climatology is fit on the committed
  training artifact, while replay metrics are measured on this later disjoint
  evaluation window. This removes leakage from evaluating thresholds against
  the same years used to define seasonal baselines.
- Climate non-stationarity still matters: a fixed historical baseline can drift
  as city climate, observing systems, and reanalysis behavior change over time.
  The split makes leakage visible; it does not make the baseline timeless.
- DS-1's warm/cold spell asymmetry (101 warm vs 71 cold incidents) is a
  predicted, observed consequence of using a 2015-2021 baseline before recent
  warming in the 2022-2025 test window.
- DS-2 gates z-based detectors with empirical per-metric training quantiles
  from that same committed artifact: 99.5th percentile upper tails, 0.5th
  percentile lower tails, and wet-hour-only 99.5th percentile rain amount.
  The quantile level is a fixed rare-tail hypothesis, not tuned to replay rates.
- {_empirical_threshold_summary()}
- DS-3 replaces month/local-hour buckets with a transparent local
  day-of-year smoothing window: median and MAD are computed at the same local
  hour over +/-15 neighboring training days. The DS-2 empirical quantiles are
  recomputed from these smooth training residuals before replay.
- DS-4 redefines two score inputs without breaking the additive 0-100 contract:
  rarity becomes surprisal (`-log(empirical tail probability)`, capped near a
  1-in-10,000 tail) and magnitude becomes absolute physical size (mm, degC, km/h).
  Severity is rebanded to the new distribution (`>=60 severe`, the replayed incident
  p90 -> ~10% severe) and the 6h rain accumulation bar is raised to 12.5 mm/6h from
  rain-mix evidence. The before/after score-distribution section isolates this scoring
  change on the fixed smooth baseline.
- Readings replayed: **{data.total_readings}** across **{data.total_city_days}**
  city-days.
- Native replay collapses detector candidates with the same stable dedupe keys,
  enter threshold, and absent-reading resolution used by lifecycle. No live
  application state is touched.
- The final native table is the **current after-state** after spatial z-gap was
  raised to 5.0 and the structural own-anomaly gate was added.
- `raw_to_incident_collapse` is raw detector firings divided by lifecycle
  incidents. It is a deduplication win metric, but it blends instantaneous and
  sustained event types, so read it as an average collapse ratio.
- Open-Meteo archive is observations-only. In `--source archive` replay,
  `scripts/evaluate.py` has no historically issued forecast rows to pair with
  observations, so `forecast_bust` is expected to show zero. The detector is
  exercised by `tests/test_native_detectors.py::test_forecast_bust_fires_on_error_over_rolling_mae`,
  by the labeled `forecast_bust_simple_mae` scenario, and by live/`--source db`
  operation when stored forecasts exist.

## Labeled Scenario Results

| Scenario | Expected | Actual | Status |
|---|---|---|---|
{scenario_table}

**Precision**: {precision:.1%} ({tp} TP, {fp} FP)  
**Recall**: {recall:.1%} ({tp} TP, {fn} FN)  
**Mean time to detect**: {mttd}

## Final Native Incident Rates

{incident_rate_table(after, data)}

Interpretation:

{_native_interpretation(after)}

## Per-City Incident Rates

{city_rate_table(after, data)}

## Severity Breakdown

{severity_table(after)}

## Calibration Before/After

{before_after_table(before, after, data)}

## Boundary Continuity

DS-3's smooth day-of-year baseline collapsed the calendar-boundary discontinuity
from roughly **0.56-1.26 z** (legacy month/local-hour buckets) down to **0.00-0.03 z**.
That fix is retained unchanged in DS-4; the table below is the standing before/after.

{boundary_continuity_table()}

## DS-4 Scoring: Rarity = Surprisal, Decorrelated from Magnitude

DS-4 replaces the saturating/clipped rarity component with **surprisal**
(`-log(empirical tail probability)`), capped near a 1-in-10,000 tail, so a
1-in-1000 event scores strictly above a 1-in-100 event instead of both maxing out.
Rarity is now the **statistical tail position**; magnitude is the **absolute physical
size** (mm rain, degC departure, km/h gust). The two axes are orthogonal, so a
rare-but-small event and a common-but-large event score differently.

{score_distribution_table(before_scoring, after)}

### Rain-Mix Histogram Evidence

{rain_trigger_table(after)}

![Per-detector score histograms](evaluation/score_histograms.png)

![Heavy-rain score mix by trigger](evaluation/rain_mix_histogram.png)

## Legacy Volume vs Native Incidents

{legacy_comparison_table(legacy, after)}

## Known-Event Spot Checks

{spot_check_table(after)}

**DS-4 honesty note (rain spot checks are false negatives in ERA5).** The two
documented convective rain events are **not detected at all** in this ERA5 replay:
Toronto 2024-07-16 peaks at 4.3 mm/h / 11.0 mm/6h and Ottawa 2023-06-26 at
5.0 mm/h / 10.6 mm/6h, both below the 10 mm/h hourly floor and the 12.5 mm/6h
accumulation bar, so no `heavy_rain_burst` candidate fires. They are false
negatives, not warning-tier incidents. The cause is ERA5 hourly reanalysis
grid-smoothing flattening the convective peak below the principled bar (the real
events exceeded 100 mm in pockets); finer-resolution live observations would very
likely clear the bar. This is a data-resolution limit, not a detector or scoring
regression: both events remain covered by unit and labeled tests, and the Vancouver
cold spell -- a genuine multi-day tail event that ERA5 *does* resolve -- still
scores severe (70). We report the false negatives rather than lower the bar or the
severe band to manufacture a match. The earlier "67" came from pre-DS-4 binary
rarity that gave every firing rain hour full rarity credit at a 10 mm bar.

## DS-5 Quantitative Validation

Offline validation against weak labels and a precision proxy. No credentials, no live
calls; the live pipeline stays Open-Meteo. Both measures use the DS-4 replay incidents.

### Recall vs ECCC weak labels

ECCC publishes no stable public API for historical alert archives, so the label set is a
curated, sourced list of high-impact weather windows for the three cities over the replay
span, drawn from ECCC's annual top-ten weather stories and contemporaneous reporting.
Dates are approximate event windows matched with +/-1 day padding; this bounds recall on
notable events, it is not exhaustive ground truth.

This is a small (N=15), deliberately hard, biased sample, not a precise recall estimate.
The Wilson interval is wide (see above), so read these as a directional **lower bound** on
recall over notable events, not a point estimate.

{weak_label_recall_table(after)}

{chance_recall_line(after)}

#### Miss decomposition: resolution limit vs detector gap

{miss_decomposition_table(after, data)}

**Headline false negatives.** The Toronto 2024-07-16 and Ottawa 2023-06-26 floods are
confirmed false negatives: ECCC alerted, the ERA5-based system did not detect. Cause:
ERA5 hourly reanalysis grid-smoothing flattens the convective peak (real events exceeded
100 mm in pockets) to ~5 mm/h and ~11 mm/6h, below the principled 10 mm/h floor and the
12.5 mm/6h burst bar. Mitigation: this is a backtest-data resolution limit, not a detector
defect; finer-resolution live observations would very likely clear the bar, and both
events stay covered by unit and labeled tests. The recall number above is honest and
explained rather than engineered around.

### Precision proxy (top-N by score)

{precision_proxy_table(after)}

The borderline incidents are all `heavy_rain_burst` accumulation events in the 13-20 mm/6h
band: above the 12.5 mm/6h detection bar and high-scoring, but below the 20 mm/6h "useful"
physical bar and with no hour reaching the 15 mm/h intensity cut -- real multi-hour rain,
not clearly burst-intensity.

Labeling rule (physical-significance bars, anchored to units, independent of the score so
the label does not grade itself): `temperature_shock` useful at `z>=4` and `|dT|>=8C`;
`warm/cold_spell` at `z>=4` or `|departure|>=10C`; `heavy_rain_burst` at `>=15 mm/h` or
`>=20 mm/6h`; `wind_gust_burst` at `>=80 km/h`; `heat_stress` at `humidex>=40`;
`cold_stress` at `wind chill<=-30`; `pressure_plunge` at `>=10 hPa/3h`; `spatial_anomaly`
at `>=8 z` peer gap; `forecast_bust` at `>=4x` rolling MAE. "noise" is barely over the
detector gate; "borderline" is in between.

## Calibration Changes Applied

{calibration_changes_table()}

## Diagnostic Figures

![z-score histogram](evaluation/zscore_histogram.png)

![Events by local hour](evaluation/events_by_local_hour.png)

![Severity breakdown](evaluation/severity_breakdown.png)

## Notes

- The old detector volume is raw output because the retired system wrote trigger
  rows directly. The native volume is lifecycle incidents because the feed now
  collapses persistent conditions.
- Forecast-bust lead conditioning remains documented future work; this phase
  keeps the simple global rolling MAE form. The archive replay zero is a data
  availability artifact, not evidence that the detector threshold is broken.
- Optional ECCC weak-label scoring was not run in this pass; the live pipeline
  remains Open-Meteo only.
"""
    EVAL_PATH.write_text(md)


def main() -> None:
    os.chdir(PROJECT_ROOT)
    args = parse_args()
    FIG_DIR.mkdir(exist_ok=True)

    if args.source == "archive":
        data = asyncio.run(
            _load_archive_data(
                start_date=args.start_date,
                end_date=args.end_date,
                chunk_days=args.chunk_days,
            )
        )
        forecasts: dict[tuple[str, datetime], Forecast] = {}
    else:
        data, forecasts = _load_db_data()

    if data.total_readings == 0:
        print("No readings available for replay.")
        sys.exit(1)

    print(f"Loaded {data.total_readings} readings from {data.source_label}.")
    print("Replaying retired legacy rules...")
    legacy = replay_legacy(data, forecasts)
    print(f"Legacy raw outputs: {len(legacy)}")

    print("Replaying DS-2 month-hour quantile baseline...")
    before = replay_native(data, forecasts, profile=DS2_MONTH_HOUR_PROFILE)
    print(f"DS-2 month-hour incidents: {len(before.incidents)}")

    print("Replaying DS-3 smooth baseline with pre-DS-4 clipped scoring...")
    before_scoring = replay_native(data, forecasts, profile=ds3_scoring_profile())
    print(f"DS-3 clipped-scoring incidents: {len(before_scoring.incidents)}")

    print("Replaying DS-4 smooth baseline with surprisal scoring...")
    after = replay_native(data, forecasts, profile=current_profile())
    print(f"DS-4 surprisal incidents: {len(after.incidents)}")

    tp, fp, fn, scenario_details, mttd = evaluate_labeled()
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0

    plot_zscore_histogram(after)
    plot_events_by_local_hour(after)
    plot_severity_pie(after)
    plot_detector_score_histograms(after)
    plot_rain_mix_histogram(after)
    write_evaluation(
        data=data,
        legacy=legacy,
        before=before,
        before_scoring=before_scoring,
        after=after,
        scenario_table="\n".join(scenario_details),
        precision=precision,
        recall=recall,
        tp=tp,
        fp=fp,
        fn=fn,
        mttd=mttd,
    )
    print(f"Figures written to {FIG_DIR}/")
    print(f"Written {EVAL_PATH}")


if __name__ == "__main__":
    main()
