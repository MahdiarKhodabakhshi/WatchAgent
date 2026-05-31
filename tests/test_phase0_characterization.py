from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.detection import detect
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
    seed_event(db_session, reading, event_type="rapid_change")

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
    assert event_payload["event_type"] == "rapid_change"
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
    history = [
        _reading(
            id=idx + 1,
            hours_offset=-(idx + 3),
            temperature_2m=20.0 + (idx % 3),
            apparent_temperature=21.0 + (idx % 3),
            wind_speed_10m=5.0 + (idx % 5) * 5.0,
            weather_code=0,
        )
        for idx in range(20)
    ]
    history.extend(
        [
            _reading(
                id=30,
                hours_offset=-2,
                temperature_2m=21.0,
                apparent_temperature=22.0,
                wind_speed_10m=25.0,
                weather_code=0,
            ),
            _reading(
                id=31,
                hours_offset=-1,
                temperature_2m=22.0,
                apparent_temperature=23.0,
                wind_speed_10m=26.0,
                weather_code=0,
            ),
        ]
    )
    current = _reading(
        id=100,
        temperature_2m=28.0,
        apparent_temperature=40.0,
        wind_speed_10m=27.0,
        weather_code=95,
    )
    peer = _reading(
        id=200,
        city="Toronto",
        temperature_2m=5.0,
        apparent_temperature=5.0,
        wind_speed_10m=10.0,
        weather_code=0,
    )
    forecast = FakeForecast(weather_code=0, temperature_2m=20.0, lead_hours=6)

    events = detect(current, history, peers={"Toronto": peer}, forecast=forecast)

    assert [
        (event.event_type, event.severity, event.metric)
        for event in events
    ] == [
        ("wmo_transition", "severe", "weather_code"),
        ("rapid_change", "severe", "temperature_2m"),
        ("sustained_extreme", "warning", "wind_speed_10m"),
        ("comfort_divergence", "severe", "apparent_temperature"),
        ("cross_city_contrast", "warning", "temperature_2m"),
        ("cross_city_contrast", "warning", "apparent_temperature"),
        ("cross_city_contrast", "warning", "wind_speed_10m"),
        ("forecast_divergence", "severe", "weather_code"),
        ("forecast_divergence", "warning", "temperature_2m"),
        ("fun_fact", "info", "temperature_2m"),
    ]

    rapid = _only(events, "rapid_change", "temperature_2m")
    assert rapid.signal_values["z_score"] == 8.775
    assert rapid.signal_values["baseline_kind"] == "rolling_24h"
    assert "8.8 sigma" in rapid.reason

    sustained = _only(events, "sustained_extreme", "wind_speed_10m")
    assert sustained.signal_values["tail"] == "upper"
    assert sustained.signal_values["threshold"] == 25.0

    comfort = _only(events, "comfort_divergence", "apparent_temperature")
    assert comfort.signal_values["gap"] == 12.0
    assert comfort.signal_values["threshold"] == 1.0

    forecast_temp = _only(events, "forecast_divergence", "temperature_2m")
    assert forecast_temp.signal_values["abs_error"] == 8.0
    assert forecast_temp.signal_values["lead_hours"] == 6

    fun_fact = _only(events, "fun_fact", "temperature_2m")
    assert fun_fact.signal_values["kind"] == "warm_record"
    assert fun_fact.signal_values["previous_record_temperature_2m"] == 22.0


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
    )


def _only(events: list, event_type: str, metric: str):
    matches = [
        event
        for event in events
        if event.event_type == event_type and event.metric == metric
    ]
    assert len(matches) == 1
    return matches[0]
