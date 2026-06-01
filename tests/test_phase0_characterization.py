from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.detection.base import DetectorContext
from app.detection.registry import detect_candidates
from app.features import Climatology
from app.models import Reading
from app.storage import store_reading_if_new
from tests.conftest import seed_event, seed_reading

BASE_TS = datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc)

READING_KEYS = {
    "id",
    "city",
    "observation_ts",
    "polled_at",
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "wind_speed_10m",
    "weather_code",
}
EVENT_KEYS = {
    "id",
    "city",
    "event_ts",
    "created_at",
    "event_type",
    "severity",
    "metric",
    "signal_values",
    "reason",
    "supporting_reading_ids",
}


@dataclass(frozen=True)
class FakeForecast:
    weather_code: int | None
    temperature_2m: float | None
    lead_hours: int
    precipitation: float | None = None
    wind_speed_10m: float | None = None


def test_phase0_api_shape_keeps_current_required_fields(client, db_session: Session) -> None:
    reading = seed_reading(db_session, city="Toronto", hours_offset=0)
    seed_event(db_session, reading, event_type="temperature_shock")

    health_response = client.get("/health")
    readings_response = client.get("/readings?city=Toronto&limit=1")
    events_response = client.get("/events?city=Toronto&limit=1")

    assert health_response.status_code == 200
    assert health_response.json() == {
        "status": "ok",
        "readings_stored": 1,
        "events_stored": 1,
    }

    assert readings_response.status_code == 200
    reading_payload = readings_response.json()["readings"][0]
    assert READING_KEYS <= set(reading_payload)
    assert reading_payload["city"] == "Toronto"
    assert reading_payload["temperature_2m"] == 20.0

    assert events_response.status_code == 200
    event_payload = events_response.json()["events"][0]
    assert EVENT_KEYS <= set(event_payload)
    assert event_payload["event_type"] == "temperature_shock"
    assert event_payload["severity"] == "warning"
    assert event_payload["signal_values"]["z_score"] == 3.0


def test_phase0_dedup_same_city_timestamp_stores_one_row(db_session: Session) -> None:
    reading_data = {
        "city": "Ottawa",
        "observation_ts": BASE_TS,
        "polled_at": BASE_TS + timedelta(minutes=5),
        "temperature_2m": 21.5,
        "apparent_temperature": 20.9,
        "precipitation": 0.0,
        "wind_speed_10m": 11.0,
        "weather_code": 1,
    }

    first = store_reading_if_new(db_session, reading_data)
    second = store_reading_if_new(db_session, reading_data)
    db_session.commit()

    rows = db_session.scalars(select(Reading).where(Reading.city == "Ottawa")).all()
    assert first is not None
    assert second is None
    assert len(rows) == 1
    assert rows[0].observation_ts == BASE_TS


def test_phase0_pure_detection_outputs_for_fixed_sequence() -> None:
    history = _history(
        {
            -3: {
                "temperature_2m": 22.0,
                "pressure_msl": 1007.0,
                "wind_gusts_10m": 35.0,
            }
        }
    )
    current = _reading(
        id=100,
        temperature_2m=28.0,
        precipitation=18.0,
        pressure_msl=1000.0,
        wind_gusts_10m=55.0,
    )
    peer = _reading(
        id=200,
        city="Toronto",
        temperature_2m=20.0,
        precipitation=18.0,
        pressure_msl=1000.0,
        wind_gusts_10m=55.0,
    )
    forecast = FakeForecast(weather_code=0, temperature_2m=20.0, lead_hours=6)

    events = detect_candidates(
        DetectorContext(
            reading=current,
            history=history,
            peers={"Toronto": peer},
            forecast=forecast,
            climatology=Climatology(_mini_climatology()),
            forecast_comparison_pairs=_forecast_pairs(),
        )
    )

    assert [
        (event.event_type, event.severity, event.metric)
        for event in events
    ] == [
        ("temperature_shock", "severe", "temperature_2m"),
        ("pressure_plunge", "warning", "pressure_msl"),
        ("warm_spell", "warning", "temperature_2m"),
        ("heavy_rain_burst", "warning", "precipitation"),
        ("wind_gust_burst", "warning", "wind_gusts_10m"),
        ("forecast_bust", "warning", "temperature_2m"),
        ("spatial_anomaly", "warning", "temperature_2m"),
    ]

    shock = _only(events, "temperature_shock", "temperature_2m")
    assert shock.signal_values["z_score"] == 4.0
    assert shock.signal_values["delta_c"] == 6.0

    pressure = _only(events, "pressure_plunge", "pressure_msl")
    assert pressure.signal_values["pressure_fall_hpa"] == 7.0
    assert pressure.signal_values["wind_rise_kmh"] == 20.0

    forecast_temp = _only(events, "forecast_bust", "temperature_2m")
    assert forecast_temp.signal_values["abs_error"] == 8.0
    assert forecast_temp.signal_values["normalized_error"] == 8.0


def _reading(
    *,
    id: int,
    city: str = "Ottawa",
    hours_offset: int = 0,
    temperature_2m: float = 20.0,
    apparent_temperature: float | None = None,
    precipitation: float = 0.0,
    wind_speed_10m: float = 10.0,
    weather_code: int = 0,
    pressure_msl: float = 1010.0,
    dew_point_2m: float = 10.0,
    wind_gusts_10m: float = 20.0,
) -> Reading:
    observation_ts = BASE_TS + timedelta(hours=hours_offset)
    return Reading(
        id=id,
        city=city,
        observation_ts=observation_ts,
        polled_at=observation_ts + timedelta(minutes=5),
        temperature_2m=temperature_2m,
        apparent_temperature=(
            temperature_2m if apparent_temperature is None else apparent_temperature
        ),
        precipitation=precipitation,
        wind_speed_10m=wind_speed_10m,
        weather_code=weather_code,
        pressure_msl=pressure_msl,
        dew_point_2m=dew_point_2m,
        wind_gusts_10m=wind_gusts_10m,
    )


def _only(events: list, event_type: str, metric: str):
    matches = [
        event
        for event in events
        if event.event_type == event_type and event.metric == metric
    ]
    assert len(matches) == 1
    return matches[0]


def _history(overrides: dict[int, dict] | None = None) -> list[Reading]:
    overrides = overrides or {}
    return [
        _reading(
            id=idx,
            hours_offset=-idx,
            **overrides.get(-idx, {}),
        )
        for idx in range(1, 13)
    ]


def _forecast_pairs():
    return (
        (_reading(id=301, temperature_2m=21.0), FakeForecast(None, 20.0, 6)),
        (_reading(id=302, temperature_2m=19.0), FakeForecast(None, 20.0, 6)),
        (_reading(id=303, temperature_2m=20.5), FakeForecast(None, 19.5, 6)),
    )


def _stats(median: float, scale: float) -> dict:
    return {"n": 120, "median": median, "mad": scale / 1.4826, "scale": scale}


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
            "Ottawa": {
                "5": {
                    "14": {
                        "temperature_2m": _stats(20.0, 2.0),
                        "wind_gusts_10m": _stats(20.0, 10.0),
                        "pressure_msl": _stats(1010.0, 2.0),
                        "precipitation": _stats(0.0, 1.0),
                    }
                }
            },
            "Toronto": {
                "5": {
                    "14": {
                        "temperature_2m": _stats(20.0, 2.0),
                        "wind_gusts_10m": _stats(20.0, 10.0),
                        "pressure_msl": _stats(1010.0, 2.0),
                        "precipitation": _stats(0.0, 1.0),
                    }
                }
            },
        },
        "fallbacks": {"month": {}, "city": {}},
        "precipitation": {
            "wet_threshold_mm": 0.1,
            "buckets": {
                "Ottawa": {
                    "5": {
                        "14": {
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
            },
            "fallbacks": {"month": {}, "city": {}},
        },
    }
