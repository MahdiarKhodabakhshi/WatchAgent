from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.detection.base import DetectorContext
from app.detection.forecast_bust import ForecastBustDetector
from app.detection.heavy_rain_burst import HeavyRainBurstDetector
from app.detection.pressure_plunge import PressurePlungeDetector
from app.detection.spatial_anomaly import SpatialAnomalyDetector
from app.detection.spells import ColdSpellDetector, WarmSpellDetector
from app.detection.stress import ColdStressDetector, HeatStressDetector
from app.detection.temperature_shock import TemperatureShockDetector
from app.detection.wind_gust_burst import WindGustBurstDetector
from app.features import Climatology

BASE_TS = datetime(2026, 6, 1, 16, 0, tzinfo=timezone.utc)  # 12:00 local Toronto


def test_temperature_shock_fires_on_local_hour_z_and_rate() -> None:
    detector = TemperatureShockDetector()
    current = _reading(id=100, temperature_2m=28.0)
    history = _history({-3: {"temperature_2m": 22.0}})

    events = detector.detect(_ctx(current, history))

    assert len(events) == 1
    assert events[0].event_type == "temperature_shock"
    assert events[0].signal_values["z_score"] == 4.0
    assert events[0].signal_values["delta_c"] == 6.0
    assert events[0].dedupe_key == "Toronto|temperature_shock|temperature_2m"


def test_temperature_shock_near_miss_requires_z_and_rate() -> None:
    detector = TemperatureShockDetector()
    current = _reading(id=100, temperature_2m=24.0)
    history = _history({-3: {"temperature_2m": 22.0}})

    assert detector.detect(_ctx(current, history)) == []


def test_temperature_shock_cold_start_does_not_fire() -> None:
    detector = TemperatureShockDetector()
    current = _reading(id=100, temperature_2m=32.0)
    history = _history({-3: {"temperature_2m": 20.0}}, count=11)

    assert detector.detect(_ctx(current, history)) == []


@pytest.mark.parametrize(
    ("detector", "event_type", "temperature"),
    [
        (WarmSpellDetector(), "warm_spell", 26.0),
        (ColdSpellDetector(), "cold_spell", 14.0),
    ],
)
def test_warm_and_cold_spell_fire_on_temperature_z(
    detector,
    event_type: str,
    temperature: float,
) -> None:
    events = detector.detect(_ctx(_reading(id=100, temperature_2m=temperature), _history()))

    assert len(events) == 1
    assert events[0].event_type == event_type
    assert events[0].metric == "temperature_2m"
    assert events[0].signal_values["z_score"] == 3.0


@pytest.mark.parametrize(
    ("detector", "temperature"),
    [
        (WarmSpellDetector(), 24.0),
        (ColdSpellDetector(), 16.0),
    ],
)
def test_warm_and_cold_spell_near_miss_does_not_fire(detector, temperature: float) -> None:
    assert detector.detect(_ctx(_reading(id=100, temperature_2m=temperature), _history())) == []


@pytest.mark.parametrize("detector", [WarmSpellDetector(), ColdSpellDetector()])
def test_warm_and_cold_spell_cold_start_does_not_fire(detector) -> None:
    current = _reading(id=100, temperature_2m=40.0)

    assert detector.detect(_ctx(current, _history(count=11))) == []


def test_pressure_plunge_fires_on_three_hour_fall_confirmed_by_wind() -> None:
    detector = PressurePlungeDetector()
    current = _reading(id=100, pressure_msl=1000.0, wind_gusts_10m=45.0)
    history = _history(
        {
            -3: {"pressure_msl": 1007.0, "wind_gusts_10m": 35.0},
            -6: {"pressure_msl": 1010.0, "wind_gusts_10m": 30.0},
        },
        pressure_msl=1010.0,
        wind_gusts_10m=30.0,
    )

    events = detector.detect(_ctx(current, history))

    assert len(events) == 1
    assert events[0].event_type == "pressure_plunge"
    assert events[0].signal_values["pressure_fall_hpa"] == 7.0
    assert events[0].signal_values["wind_rise_kmh"] == 10.0


def test_pressure_plunge_near_miss_requires_rising_wind() -> None:
    detector = PressurePlungeDetector()
    current = _reading(id=100, pressure_msl=1000.0, wind_gusts_10m=31.0)
    history = _history({-3: {"pressure_msl": 1007.0, "wind_gusts_10m": 30.0}})

    assert detector.detect(_ctx(current, history)) == []


def test_pressure_plunge_cold_start_does_not_fire() -> None:
    detector = PressurePlungeDetector()
    current = _reading(id=100, pressure_msl=995.0, wind_gusts_10m=60.0)
    history = _history({-3: {"pressure_msl": 1010.0, "wind_gusts_10m": 30.0}}, count=11)

    assert detector.detect(_ctx(current, history)) == []


def test_heavy_rain_burst_fires_on_wet_hour_amount() -> None:
    detector = HeavyRainBurstDetector()
    current = _reading(id=100, precipitation=12.0)

    events = detector.detect(_ctx(current, _history()))

    assert len(events) == 1
    assert events[0].event_type == "heavy_rain_burst"
    assert events[0].signal_values["amount_mm"] == 12.0
    assert events[0].signal_values["threshold_mm"] == 10.0


def test_heavy_rain_burst_near_miss_does_not_fire() -> None:
    detector = HeavyRainBurstDetector()
    current = _reading(id=100, precipitation=8.0)

    assert detector.detect(_ctx(current, _history())) == []


def test_heavy_rain_burst_dry_hours_never_fire() -> None:
    detector = HeavyRainBurstDetector()
    current = _reading(id=100, precipitation=0.0)

    assert detector.detect(_ctx(current, _history())) == []


def test_heavy_rain_burst_cold_start_does_not_fire() -> None:
    detector = HeavyRainBurstDetector()
    current = _reading(id=100, precipitation=40.0)

    assert detector.detect(_ctx(current, _history(count=11))) == []


def test_wind_gust_burst_fires_on_gust_anomaly() -> None:
    detector = WindGustBurstDetector()
    current = _reading(id=100, wind_gusts_10m=55.0)

    events = detector.detect(_ctx(current, _history()))

    assert len(events) == 1
    assert events[0].event_type == "wind_gust_burst"
    assert events[0].signal_values["z_score"] == 3.5


def test_wind_gust_burst_near_miss_does_not_fire() -> None:
    detector = WindGustBurstDetector()
    current = _reading(id=100, wind_gusts_10m=45.0)

    assert detector.detect(_ctx(current, _history())) == []


def test_wind_gust_burst_cold_start_does_not_fire() -> None:
    detector = WindGustBurstDetector()
    current = _reading(id=100, wind_gusts_10m=90.0)

    assert detector.detect(_ctx(current, _history(count=11))) == []


def test_heat_stress_fires_on_humidex() -> None:
    detector = HeatStressDetector()
    current = _reading(id=100, temperature_2m=31.0, dew_point_2m=25.0)

    events = detector.detect(_ctx(current, _history()))

    assert len(events) == 1
    assert events[0].event_type == "heat_stress"
    assert events[0].metric == "humidex"
    assert events[0].signal_values["humidex"] >= 40.0


def test_heat_stress_near_miss_does_not_fire() -> None:
    detector = HeatStressDetector()
    current = _reading(id=100, temperature_2m=28.0, dew_point_2m=18.0)

    assert detector.detect(_ctx(current, _history())) == []


def test_heat_stress_cold_start_does_not_fire() -> None:
    detector = HeatStressDetector()
    current = _reading(id=100, temperature_2m=34.0, dew_point_2m=27.0)

    assert detector.detect(_ctx(current, _history(count=11))) == []


def test_cold_stress_fires_on_wind_chill() -> None:
    detector = ColdStressDetector()
    current = _reading(id=100, temperature_2m=-20.0, wind_speed_10m=30.0)

    events = detector.detect(_ctx(current, _history()))

    assert len(events) == 1
    assert events[0].event_type == "cold_stress"
    assert events[0].metric == "wind_chill"
    assert events[0].signal_values["wind_chill"] <= -25.0


def test_cold_stress_near_miss_does_not_fire() -> None:
    detector = ColdStressDetector()
    current = _reading(id=100, temperature_2m=-8.0, wind_speed_10m=15.0)

    assert detector.detect(_ctx(current, _history())) == []


def test_cold_stress_cold_start_does_not_fire() -> None:
    detector = ColdStressDetector()
    current = _reading(id=100, temperature_2m=-30.0, wind_speed_10m=40.0)

    assert detector.detect(_ctx(current, _history(count=11))) == []


def test_forecast_bust_fires_on_error_over_rolling_mae() -> None:
    detector = ForecastBustDetector()
    current = _reading(id=100, temperature_2m=30.0)
    forecast = SimpleNamespace(temperature_2m=20.0, lead_hours=6)

    events = detector.detect(
        _ctx(
            current,
            _history(),
            forecast=forecast,
            forecast_comparison_pairs=_forecast_pairs(),
        )
    )

    assert len(events) == 1
    assert events[0].event_type == "forecast_bust"
    assert events[0].metric == "temperature_2m"
    assert events[0].signal_values["normalized_error"] == 10.0


def test_forecast_bust_near_miss_does_not_fire() -> None:
    detector = ForecastBustDetector()
    current = _reading(id=100, temperature_2m=21.5)
    forecast = SimpleNamespace(temperature_2m=20.0, lead_hours=6)

    assert (
        detector.detect(
            _ctx(
                current,
                _history(),
                forecast=forecast,
                forecast_comparison_pairs=_forecast_pairs(),
            )
        )
        == []
    )


def test_forecast_bust_cold_start_does_not_fire() -> None:
    detector = ForecastBustDetector()
    current = _reading(id=100, temperature_2m=35.0)
    forecast = SimpleNamespace(temperature_2m=20.0, lead_hours=6)

    assert (
        detector.detect(
            _ctx(
                current,
                _history(count=11),
                forecast=forecast,
                forecast_comparison_pairs=_forecast_pairs(),
            )
        )
        == []
    )


def test_spatial_anomaly_fires_on_peer_z_gap() -> None:
    detector = SpatialAnomalyDetector()
    current = _reading(id=100, temperature_2m=32.0)
    peers = {
        "Ottawa": _reading(id=200, city="Ottawa", temperature_2m=20.0),
        "Vancouver": _reading(id=201, city="Vancouver", temperature_2m=15.0),
    }

    events = detector.detect(_ctx(current, _history(), peers=peers))

    assert len(events) == 1
    assert events[0].event_type == "spatial_anomaly"
    assert events[0].metric == "temperature_2m"
    assert events[0].signal_values["difference"] == 6.0


def test_spatial_anomaly_near_miss_does_not_fire() -> None:
    detector = SpatialAnomalyDetector()
    current = _reading(id=100, temperature_2m=25.0)
    peers = {
        "Ottawa": _reading(id=200, city="Ottawa", temperature_2m=20.0),
        "Vancouver": _reading(id=201, city="Vancouver", temperature_2m=15.0),
    }

    assert detector.detect(_ctx(current, _history(), peers=peers)) == []


def test_spatial_anomaly_cold_start_does_not_fire() -> None:
    detector = SpatialAnomalyDetector()
    current = _reading(id=100, temperature_2m=30.0)
    peers = {"Ottawa": _reading(id=200, city="Ottawa", temperature_2m=20.0)}

    assert detector.detect(_ctx(current, _history(count=11), peers=peers)) == []


def _ctx(
    reading,
    history,
    *,
    peers=None,
    forecast=None,
    forecast_comparison_pairs=(),
) -> DetectorContext:
    return DetectorContext(
        reading=reading,
        history=history,
        peers=peers,
        forecast=forecast,
        climatology=Climatology(_mini_climatology()),
        forecast_comparison_pairs=tuple(forecast_comparison_pairs),
    )


def _reading(
    *,
    id: int,
    city: str = "Toronto",
    hours_offset: int = 0,
    temperature_2m: float = 20.0,
    apparent_temperature: float | None = None,
    precipitation: float = 0.0,
    wind_speed_10m: float = 10.0,
    weather_code: int = 0,
    pressure_msl: float = 1010.0,
    surface_pressure: float | None = None,
    relative_humidity_2m: float = 50.0,
    dew_point_2m: float = 10.0,
    wind_gusts_10m: float = 20.0,
    cloud_cover: float = 10.0,
):
    ts = BASE_TS + timedelta(hours=hours_offset)
    return SimpleNamespace(
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
        pressure_msl=pressure_msl,
        surface_pressure=surface_pressure,
        relative_humidity_2m=relative_humidity_2m,
        dew_point_2m=dew_point_2m,
        wind_gusts_10m=wind_gusts_10m,
        cloud_cover=cloud_cover,
    )


def _history(overrides: dict[int, dict] | None = None, *, count: int = 12, **defaults):
    overrides = overrides or {}
    return [
        _reading(
            id=idx,
            hours_offset=-idx,
            **{
                **defaults,
                **overrides.get(-idx, {}),
            },
        )
        for idx in range(1, count + 1)
    ]


def _forecast_pairs():
    return (
        (_reading(id=301, temperature_2m=11.0), SimpleNamespace(temperature_2m=10.0)),
        (_reading(id=302, temperature_2m=14.0), SimpleNamespace(temperature_2m=13.0)),
        (_reading(id=303, temperature_2m=18.0), SimpleNamespace(temperature_2m=17.0)),
    )


def _stats(median: float, scale: float) -> dict:
    return {"n": 120, "median": median, "mad": scale / 1.4826, "scale": scale}


def _metric_buckets(city: str, *, temp_median: float) -> dict:
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
    city_buckets = {
        "Toronto": _metric_buckets("Toronto", temp_median=20.0),
        "Ottawa": _metric_buckets("Ottawa", temp_median=20.0),
        "Vancouver": _metric_buckets("Vancouver", temp_median=15.0),
    }
    return {
        "metric_epsilons": {
            "temperature_2m": 0.5,
            "wind_gusts_10m": 1.0,
            "pressure_msl": 0.5,
            "precipitation": 0.1,
        },
        "min_bucket_n": 30,
        "buckets": city_buckets,
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
                                "75": 2.5,
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
