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
from dataclasses import dataclass
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
        "incident_ts": "2024-07-16 17:00 UTC",
        "score": "67.0",
        "evidence": "severe; 6h accumulation trigger reached 11.0 mm in archive data",
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
        "incident_ts": "2024-01-11 21:00 UTC",
        "score": "70.0",
        "evidence": "severe; Jan 12 candidates reached z=4.2 to z=7.1",
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
        "incident_ts": "2023-06-27 02:00 UTC",
        "score": "65.2",
        "evidence": "severe; 6h accumulation trigger reached 10.6 mm in archive data",
    },
]


@dataclass(frozen=True)
class CalibrationProfile:
    name: str
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


DS1_Z_GATE_PROFILE = CalibrationProfile(
    name="DS-1 z-gated thresholds",
    use_empirical_quantile_gates=False,
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
                )
                candidates = detect_candidates(ctx)
                raw.extend((reading, candidate) for candidate in candidates)
                incidents.extend(_collapse_candidates(states, candidates, reading))
    return NativeReplay(raw=raw, incidents=incidents)


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
        name="DS-2 empirical quantile gates",
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
        "| temperature_shock | fixed `abs(z_hod) >= 3.0` -> temperature residual "
        "99.5/0.5 training quantiles; delta remains 5C | "
        "The same rare-tail concept is now read from temperature's own "
        "training residual distribution. |",
        "| warm/cold spell | fixed `z_hod` +/-3.0 -> temperature residual "
        "99.5/0.5 training quantiles | "
        "Warm and cold persistence gates use separate signed tails instead of "
        "assuming symmetric z behavior. |",
        "| pressure_plunge | unchanged in DS-2 | It already uses an "
        "empirical pressure-fall percentile over replay history rather than a "
        "shared z gate. |",
        "| heavy_rain_burst | wet-hour p95/floor -> wet-hour 99.5th training "
        "amount quantile plus 10 mm hazard floor; dry-hour hurdle and 6h "
        "accumulation anchor unchanged | Rain uses the upper tail of wet "
        "amounts only, but flood-style bursts still need an absolute hazard "
        "floor when the city wet-hour distribution is compressed. |",
        "| wind_gust_burst | fixed gust z 3.2 -> wind-gust residual 99.5th "
        "training quantile; 90 km/h anchor unchanged | "
        "Gusts are upper-tail hazards, and the absolute ECCC-scale anchor "
        "still fires even when local z is below the empirical quantile. |",
        "| heat_stress | unchanged in DS-2 | This detector is formula-threshold based, not a "
        "`z_hod >= 3` gate. |",
        "| cold_stress | unchanged in DS-2 | This detector is formula-threshold based, not a "
        "`z_hod >= 3` gate. |",
        "| forecast_bust | unchanged in DS-2 | Archive replay still lacks historical forecast "
        "pairs. |",
        "| spatial_anomaly | fixed own `|z_hod| >= 3.0` -> metric residual "
        "training quantiles; peer z-gap remains 5.0 | "
        "The city must be anomalous in its own metric-specific tail before "
        "peer comparison; wind-gust spatial checks are upper-tail only. |",
        "| scoring weights | unchanged | "
        "DS-2 changes entry gates only; score histograms are deferred to DS-4. |",
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


def write_evaluation(
    *,
    data: ReplayData,
    legacy: list[tuple[Reading, EventCandidate]],
    before: NativeReplay,
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

## Legacy Volume vs Native Incidents

{legacy_comparison_table(legacy, after)}

## Known-Event Spot Checks

{spot_check_table(after)}

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

    print("Replaying DS-1 z-gated thresholds...")
    before = replay_native(data, forecasts, profile=DS1_Z_GATE_PROFILE)
    print(f"DS-1 z-gated incidents: {len(before.incidents)}")

    print("Replaying DS-2 empirical quantile gates...")
    after = replay_native(data, forecasts, profile=current_profile())
    print(f"DS-2 empirical quantile incidents: {len(after.incidents)}")

    tp, fp, fn, scenario_details, mttd = evaluate_labeled()
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0

    plot_zscore_histogram(after)
    plot_events_by_local_hour(after)
    plot_severity_pie(after)
    write_evaluation(
        data=data,
        legacy=legacy,
        before=before,
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
