"""Labeled scenarios for deterministic evaluation of the detection suite.

Each scenario defines a ground-truth set of expected event types so that
precision and recall can be computed exactly. Importable by both pytest
and the offline evaluation script.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.detection.timeofday import local_hour
from app.models import Reading

BASE_TS = datetime(2026, 5, 27, 19, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class FakeForecast:
    weather_code: int | None
    temperature_2m: float | None
    lead_hours: int
    precipitation: float | None = None
    wind_speed_10m: float | None = None


@dataclass
class Scenario:
    name: str
    history: list[Reading]
    reading: Reading
    expected_types: set[str]
    peers: dict[str, Any] | None = None
    forecast: FakeForecast | None = None
    description: str = ""


def _r(
    *,
    id: int = 1,
    city: str = "Toronto",
    hours_offset: float = 0,
    temperature_2m: float = 20.0,
    apparent_temperature: float | None = None,
    precipitation: float = 0.0,
    wind_speed_10m: float = 10.0,
    weather_code: int = 0,
) -> Reading:
    ts = BASE_TS + timedelta(hours=hours_offset)
    return Reading(
        id=id,
        city=city,
        observation_ts=ts,
        polled_at=ts + timedelta(minutes=5),
        temperature_2m=temperature_2m,
        apparent_temperature=(
            temperature_2m if apparent_temperature is None else apparent_temperature
        ),
        precipitation=precipitation,
        wind_speed_10m=wind_speed_10m,
        weather_code=weather_code,
    )


def _stable_history(
    n: int = 20,
    *,
    city: str = "Toronto",
    temp: float = 20.0,
    temp_noise: float = 1.0,
    wind: float = 10.0,
    precip: float = 0.0,
    weather_code: int = 0,
    apparent_offset: float = 0.0,
) -> list[Reading]:
    """History with slight variation — baseline for most scenarios."""
    return [
        _r(
            id=i + 1,
            city=city,
            hours_offset=-(i + 1),
            temperature_2m=temp + (i % 3) * temp_noise - temp_noise,
            apparent_temperature=temp + (i % 3) * temp_noise - temp_noise + apparent_offset,
            precipitation=precip,
            wind_speed_10m=wind,
            weather_code=weather_code,
        )
        for i in range(n)
    ]


def _diurnal_history(city: str = "Toronto", days: int = 14) -> list[Reading]:
    """Step-function diurnal: warm (27-29C) during local hours 14-16, cold (14C) otherwise."""
    readings: list[Reading] = []
    for hours_ago in range(1, days * 24 + 1):
        ts = BASE_TS - timedelta(hours=hours_ago)
        lh = local_hour(city, ts)
        if lh is not None and 14 <= lh <= 16:
            temp = 27.0 + (hours_ago % 3)
        else:
            temp = 14.0
        readings.append(
            Reading(
                id=hours_ago,
                city=city,
                observation_ts=ts,
                polled_at=ts + timedelta(minutes=5),
                temperature_2m=temp,
                apparent_temperature=temp,
                precipitation=0.0,
                wind_speed_10m=10.0,
                weather_code=0,
            )
        )
    return readings


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

SCENARIOS: list[Scenario] = [
    # --- rapid_change ---
    Scenario(
        name="rapid_change_fires_severe",
        description=(
            "Temperature 6 sigma above mean → severe rapid_change plus warm-record fun_fact."
        ),
        history=_stable_history(20, temp=20.0, temp_noise=1.0),
        reading=_r(id=100, temperature_2m=26.0),
        expected_types={"rapid_change", "fun_fact"},
    ),
    Scenario(
        name="rapid_change_below_threshold",
        description="Temperature within 2.5 sigma; only a warm-record fun_fact fires.",
        history=_stable_history(20, temp=20.0, temp_noise=1.0),
        reading=_r(id=100, temperature_2m=21.2),
        expected_types={"fun_fact"},
    ),
    Scenario(
        name="rapid_change_zero_std_no_fire",
        description="All identical values → std=0, but a warm-record fun_fact fires.",
        history=[
            _r(id=i + 1, hours_offset=-(i + 1), temperature_2m=20.0)
            for i in range(20)
        ],
        reading=_r(id=100, temperature_2m=20.1),
        expected_types={"fun_fact"},
    ),
    # --- sustained_extreme ---
    Scenario(
        name="sustained_extreme_upper_tail",
        description="Current + previous two readings all above p95 → sustained_extreme.",
        history=[
            *[
                _r(
                    id=i + 1, hours_offset=-(i + 3),
                    wind_speed_10m=5.0 + (i % 5) * 5.0,
                )
                for i in range(20)
            ],
            _r(id=30, hours_offset=-2, wind_speed_10m=25.0),
            _r(id=31, hours_offset=-1, wind_speed_10m=26.0),
        ],
        reading=_r(id=100, wind_speed_10m=27.0),
        expected_types={"sustained_extreme"},
    ),
    Scenario(
        name="sustained_extreme_broken_streak",
        description="Only 2 of 3 readings in tail → no sustained_extreme.",
        history=[
            *[
                _r(
                    id=i + 1, hours_offset=-(i + 3),
                    wind_speed_10m=5.0 + (i % 5) * 5.0,
                )
                for i in range(20)
            ],
            _r(id=30, hours_offset=-2, wind_speed_10m=10.0),
            _r(id=31, hours_offset=-1, wind_speed_10m=26.0),
        ],
        reading=_r(id=100, wind_speed_10m=27.0),
        expected_types=set(),
    ),
    # --- wmo_transition ---
    Scenario(
        name="wmo_clear_to_severe",
        description="Clear(0) → thunderstorm(95): 3-level jump → wmo_transition severe.",
        history=[_r(id=1, hours_offset=-1, weather_code=0)],
        reading=_r(id=100, weather_code=95),
        expected_types={"wmo_transition"},
    ),
    Scenario(
        name="wmo_small_jump_no_event",
        description="Clear(0) → drizzle(51): 1-level jump → no event.",
        history=[_r(id=1, hours_offset=-1, weather_code=0)],
        reading=_r(id=100, weather_code=51),
        expected_types=set(),
    ),
    # --- comfort_divergence ---
    Scenario(
        name="comfort_divergence_fires",
        description="Large apparent-actual gap above threshold → comfort_divergence.",
        history=_stable_history(20, temp=20.0, apparent_offset=1.0),
        reading=_r(id=100, temperature_2m=20.0, apparent_temperature=30.0),
        expected_types={"comfort_divergence"},
    ),
    Scenario(
        name="comfort_divergence_normal_gap",
        description="Normal gap within threshold → no event.",
        history=_stable_history(20, temp=20.0, apparent_offset=1.0),
        reading=_r(id=100, temperature_2m=20.0, apparent_temperature=20.5),
        expected_types=set(),
    ),
    # --- cross_city_contrast ---
    Scenario(
        name="cross_city_contrast_fires",
        description="Ottawa reading at 22C with varied history vs Toronto peer at 5C.",
        history=[
            _r(
                id=i + 1, city="Ottawa", hours_offset=-(i + 1),
                temperature_2m=15.0 + (i % 3) * 3.0,
            )
            for i in range(20)
        ],
        reading=_r(id=100, city="Ottawa", temperature_2m=22.0),
        peers={"Toronto": _r(id=50, city="Toronto", temperature_2m=5.0)},
        expected_types={"cross_city_contrast", "fun_fact"},
    ),
    Scenario(
        name="cross_city_no_peers",
        description=(
            "No peers → no cross_city_contrast event possible; warm-record fun_fact still can fire."
        ),
        history=_stable_history(20),
        reading=_r(id=100, temperature_2m=40.0),
        peers={},
        expected_types={"rapid_change", "fun_fact"},
    ),
    # --- cold start ---
    Scenario(
        name="cold_start_short_history",
        description="Only 5 readings → statistical detectors skipped; wmo can still fire.",
        history=[
            _r(id=i + 1, hours_offset=-(i + 1), weather_code=0) for i in range(5)
        ],
        reading=_r(id=100, temperature_2m=999.0, weather_code=95),
        expected_types={"wmo_transition"},
    ),
    Scenario(
        name="cold_start_empty_history",
        description="No history → nothing fires at all.",
        history=[],
        reading=_r(id=100, temperature_2m=999.0, weather_code=95),
        expected_types=set(),
    ),
    # --- diurnal baseline (Feature 2) ---
    Scenario(
        name="diurnal_warm_afternoon_suppressed",
        description=(
            "Normal 28C afternoon with 14-day diurnal history. "
            "Diurnal baseline knows this is typical; rolling-24h would have flagged it."
        ),
        history=_diurnal_history("Toronto", days=14),
        reading=_r(id=10000, city="Toronto", temperature_2m=28.0),
        expected_types=set(),
    ),
    Scenario(
        name="diurnal_genuine_spike_fires",
        description="Extreme afternoon spike above diurnal same-hour distribution → fires.",
        history=_diurnal_history("Toronto", days=14),
        reading=_r(id=10000, city="Toronto", temperature_2m=35.0),
        expected_types={"rapid_change", "fun_fact"},
    ),
    # --- forecast_divergence (Feature 3) ---
    Scenario(
        name="forecast_clear_actual_storm",
        description="Forecast clear(0), observed thunderstorm(95) → severe forecast_divergence.",
        history=_stable_history(20),
        reading=_r(id=100, weather_code=95),
        forecast=FakeForecast(weather_code=0, temperature_2m=20.0, lead_hours=6),
        expected_types={"wmo_transition", "forecast_divergence"},
    ),
    Scenario(
        name="forecast_temp_miss",
        description="Forecast 20C, actual 28C → forecast_divergence for temp (8C > 6C threshold).",
        history=_stable_history(20),
        reading=_r(id=100, temperature_2m=28.0),
        forecast=FakeForecast(weather_code=0, temperature_2m=20.0, lead_hours=6),
        expected_types={"rapid_change", "forecast_divergence", "fun_fact"},
    ),
    Scenario(
        name="forecast_small_error_no_event",
        description="Forecast 20C, actual 22C → 2C error < 6C threshold → no forecast event.",
        history=_stable_history(20, temp=20.0, temp_noise=2.0),
        reading=_r(id=100, temperature_2m=22.0),
        forecast=FakeForecast(weather_code=0, temperature_2m=20.0, lead_hours=6),
        expected_types=set(),
    ),
]
