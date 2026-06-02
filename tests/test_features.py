from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.features import Climatology, forecast_residual, k_hour_delta, load_default_climatology

BASE_TS = datetime(2026, 6, 1, 16, 0, tzinfo=timezone.utc)  # 12:00 local in Toronto


def test_default_climatology_artifact_loads() -> None:
    climatology = load_default_climatology()

    z = climatology.z_hod("Toronto", "temperature_2m", 20.0, BASE_TS)

    assert climatology.data["date_range"] == {
        "start": "2015-01-01",
        "end": "2021-12-31",
    }
    assert z.bucket == "hod"
    assert z.n >= 100
    assert z.scale is not None
    assert z.scale > 0
    assert climatology.empirical_z_threshold("temperature_2m", "upper") is not None
    assert climatology.empirical_z_threshold("temperature_2m", "lower") is not None
    assert climatology.empirical_wet_amount_threshold() is not None


def test_z_hod_uses_local_month_hour_and_floors_zero_mad() -> None:
    climatology = Climatology(_mini_climatology())

    z = climatology.z_hod("Toronto", "temperature_2m", 21.0, BASE_TS)

    assert z.bucket == "hod"
    assert z.median == 20.0
    assert z.mad == 0.0
    assert z.scale == 0.5
    assert z.z == 2.0
    assert z.confidence == 1.0


def test_z_hod_falls_back_to_month_then_city_with_lower_confidence() -> None:
    climatology = Climatology(_mini_climatology())
    missing_hod_ts = datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc)
    missing_month_ts = datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc)

    month_z = climatology.z_hod("Toronto", "temperature_2m", 19.0, missing_hod_ts)
    city_z = climatology.z_hod("Toronto", "temperature_2m", 17.0, missing_month_ts)

    assert month_z.bucket == "month"
    assert month_z.median == 18.0
    assert month_z.confidence == 0.55
    assert city_z.bucket == "city"
    assert city_z.median == 16.0
    assert city_z.confidence == 0.35


def test_precipitation_features_use_wet_hour_distribution_only() -> None:
    climatology = Climatology(_mini_climatology())

    dry = climatology.precipitation_features("Toronto", 0.0, BASE_TS)
    wet = climatology.precipitation_features("Toronto", 2.1, BASE_TS)

    assert dry.is_wet is False
    assert dry.wet_amount_percentiles == {50: 0.8, 75: 1.5, 90: 2.0, 95: 2.5, 99: 4.0}
    assert dry.wet_amount_percentile is None
    assert dry.total_count == 120
    assert dry.wet_count == 36

    assert wet.is_wet is True
    assert wet.wet_amount_percentile == 95


def test_empirical_thresholds_are_read_from_artifact() -> None:
    climatology = Climatology(_mini_climatology())

    assert climatology.empirical_upper_quantile == 99.5
    assert climatology.empirical_lower_quantile == 0.5
    assert climatology.empirical_z_threshold("temperature_2m", "upper") == 3.4
    assert climatology.empirical_z_threshold("temperature_2m", "lower") == -3.1
    assert climatology.empirical_wet_amount_threshold() == 9.5


def test_k_hour_delta_uses_matching_history_reading() -> None:
    current = _reading(temperature_2m=24.0, hours_offset=0)
    history = [
        _reading(id=1, temperature_2m=18.0, hours_offset=-3),
        _reading(id=2, temperature_2m=20.0, hours_offset=-2),
    ]

    delta = k_hour_delta(current, history, "temperature_2m", 3)

    assert delta is not None
    assert delta.previous_reading_id == 1
    assert delta.delta == 6.0


def test_forecast_residual_uses_global_rolling_mae_floor() -> None:
    reading = _reading(temperature_2m=24.0)
    forecast = SimpleNamespace(temperature_2m=20.0)
    pairs = [
        (_reading(temperature_2m=10.0), SimpleNamespace(temperature_2m=9.0)),
        (_reading(temperature_2m=15.0), SimpleNamespace(temperature_2m=13.0)),
    ]

    residual = forecast_residual(
        reading,
        forecast,
        "temperature_2m",
        pairs,
        mae_floor=0.5,
    )

    assert residual is not None
    assert residual.residual == 4.0
    assert residual.rolling_mae == 1.5
    assert residual.normalized_error == 4.0 / 1.5
    assert residual.comparison_count == 2
    assert residual.confidence == 1.0


def _reading(
    *,
    id: int | None = None,
    city: str = "Toronto",
    temperature_2m: float = 20.0,
    hours_offset: int = 0,
) -> SimpleNamespace:
    ts = BASE_TS + timedelta(hours=hours_offset)
    return SimpleNamespace(
        id=id,
        city=city,
        observation_ts=ts,
        temperature_2m=temperature_2m,
    )


def _mini_climatology() -> dict:
    return {
        "metric_epsilons": {"temperature_2m": 0.5},
        "min_bucket_n": 30,
        "buckets": {
            "Toronto": {
                "6": {
                    "12": {
                        "temperature_2m": {
                            "n": 100,
                            "median": 20.0,
                            "mad": 0.0,
                            "scale": 0.0,
                        }
                    }
                }
            }
        },
        "fallbacks": {
            "month": {
                "Toronto": {
                    "6": {
                        "temperature_2m": {
                            "n": 100,
                            "median": 18.0,
                            "mad": 2.0,
                            "scale": 2.9652,
                        }
                    }
                }
            },
            "city": {
                "Toronto": {
                    "temperature_2m": {
                        "n": 100,
                        "median": 16.0,
                        "mad": 4.0,
                        "scale": 5.9304,
                    }
                }
            },
        },
        "empirical_thresholds": {
            "tail_probability": 0.005,
            "upper_quantile": 99.5,
            "lower_quantile": 0.5,
            "metrics": {
                "temperature_2m": {
                    "n": 1000,
                    "upper_z": 3.4,
                    "lower_z": -3.1,
                    "abs_z": 3.5,
                },
                "precipitation": {
                    "wet_count": 100,
                    "wet_amount_mm": 9.5,
                },
            },
        },
        "precipitation": {
            "wet_threshold_mm": 0.1,
            "buckets": {
                "Toronto": {
                    "6": {
                        "12": {
                            "total_count": 120,
                            "wet_count": 36,
                            "percentiles": {
                                "50": 0.8,
                                "75": 1.5,
                                "90": 2.0,
                                "95": 2.5,
                                "99": 4.0,
                            },
                        }
                    }
                }
            },
            "fallbacks": {
                "month": {},
                "city": {},
            },
        },
    }
