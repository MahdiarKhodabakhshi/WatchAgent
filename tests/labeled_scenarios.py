"""Labeled native-detector scenarios for pytest and offline evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from app.features import Climatology

BASE_TS = datetime(2026, 6, 1, 16, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Scenario:
    name: str
    history: list[Any]
    reading: Any
    expected_types: set[str]
    peers: dict[str, Any] | None = None
    forecast: Any | None = None
    forecast_comparison_pairs: tuple[tuple[Any, Any], ...] = ()
    climatology: Climatology = field(default_factory=lambda: Climatology(_mini_climatology()))
    description: str = ""


def _reading(
    *,
    id: int,
    city: str = "Toronto",
    hours_offset: int = 0,
    temperature_2m: float = 20.0,
    precipitation: float = 0.0,
    wind_speed_10m: float = 10.0,
    pressure_msl: float = 1010.0,
    dew_point_2m: float = 10.0,
    wind_gusts_10m: float = 20.0,
) -> SimpleNamespace:
    ts = BASE_TS + timedelta(hours=hours_offset)
    return SimpleNamespace(
        id=id,
        city=city,
        observation_ts=ts,
        polled_at=ts + timedelta(minutes=5),
        temperature_2m=temperature_2m,
        apparent_temperature=temperature_2m,
        precipitation=precipitation,
        wind_speed_10m=wind_speed_10m,
        weather_code=0,
        pressure_msl=pressure_msl,
        surface_pressure=None,
        dew_point_2m=dew_point_2m,
        wind_gusts_10m=wind_gusts_10m,
        relative_humidity_2m=50.0,
        cloud_cover=10.0,
    )


def _history(overrides: dict[int, dict] | None = None) -> list[SimpleNamespace]:
    overrides = overrides or {}
    return [
        _reading(id=idx, hours_offset=-idx, **overrides.get(-idx, {}))
        for idx in range(1, 13)
    ]


def _forecast_pairs() -> tuple[tuple[Any, Any], ...]:
    return (
        (_reading(id=301, temperature_2m=21.0), SimpleNamespace(temperature_2m=20.0)),
        (_reading(id=302, temperature_2m=19.0), SimpleNamespace(temperature_2m=20.0)),
        (_reading(id=303, temperature_2m=20.5), SimpleNamespace(temperature_2m=19.5)),
    )


def _stats(median: float, scale: float) -> dict:
    return {"n": 120, "median": median, "mad": scale / 1.4826, "scale": scale}


def _city_bucket(temp_median: float) -> dict:
    return {
        "6": {
            "12": {
                "temperature_2m": _stats(temp_median, 2.0),
                "wind_gusts_10m": _stats(20.0, 10.0),
                "pressure_msl": _stats(1010.0, 2.0),
                "precipitation": _stats(0.0, 1.0),
            }
        }
    }


def _mini_climatology() -> dict:
    return {
        "metric_epsilons": {
            "temperature_2m": 0.5,
            "wind_gusts_10m": 1.0,
            "pressure_msl": 0.5,
            "precipitation": 0.1,
        },
        "min_bucket_n": 30,
        "buckets": {
            "Toronto": _city_bucket(20.0),
            "Ottawa": _city_bucket(20.0),
            "Vancouver": _city_bucket(15.0),
        },
        "fallbacks": {"month": {}, "city": {}},
        "precipitation": {
            "wet_threshold_mm": 0.1,
            "buckets": {
                city: {
                    "6": {
                        "12": {
                            "total_count": 120,
                            "wet_count": 40,
                            "percentiles": {
                                "50": 1.0,
                                "75": 2.0,
                                "90": 4.0,
                                "95": 5.0,
                                "99": 15.0,
                            },
                        }
                    }
                }
                for city in ("Toronto", "Ottawa", "Vancouver")
            },
            "fallbacks": {"month": {}, "city": {}},
        },
    }


SCENARIOS: list[Scenario] = [
    Scenario(
        name="temperature_shock_and_spell",
        description="Local-hour z and 3h derivative fire temperature shock plus warm spell.",
        history=_history({-3: {"temperature_2m": 22.0}}),
        reading=_reading(id=100, temperature_2m=28.0),
        expected_types={"temperature_shock", "warm_spell"},
    ),
    Scenario(
        name="heavy_rain_wet_hour_only",
        description="Wet-hour amount exceeds local wet-hour p95 and absolute floor.",
        history=_history(),
        reading=_reading(id=101, precipitation=18.0),
        expected_types={"heavy_rain_burst"},
    ),
    Scenario(
        name="heavy_rain_dry_hour_never_fires",
        description="Dry precipitation does not become a lower-tail rain event.",
        history=_history(),
        reading=_reading(id=102, precipitation=0.0),
        expected_types=set(),
    ),
    Scenario(
        name="forecast_bust_simple_mae",
        description="Observed temperature error is normalized by recent global MAE.",
        history=_history({-3: {"temperature_2m": 28.0}}),
        reading=_reading(id=103, temperature_2m=30.0),
        forecast=SimpleNamespace(temperature_2m=20.0, lead_hours=6),
        forecast_comparison_pairs=_forecast_pairs(),
        expected_types={"forecast_bust", "warm_spell"},
    ),
    Scenario(
        name="spatial_anomaly_z_space",
        description="Peer comparison is done after each city is z-normalized.",
        history=_history({-3: {"temperature_2m": 28.0}}),
        reading=_reading(id=104, temperature_2m=28.0),
        peers={
            "Ottawa": _reading(id=201, city="Ottawa", temperature_2m=20.0),
            "Vancouver": _reading(id=202, city="Vancouver", temperature_2m=15.0),
        },
        expected_types={"warm_spell", "spatial_anomaly"},
    ),
]
