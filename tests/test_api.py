from sqlalchemy.orm import Session

from tests.conftest import BASE_TS, seed_event, seed_forecast, seed_reading


def test_health_returns_correct_shape(client, db_session: Session) -> None:
    readings = [
        seed_reading(db_session, city="Toronto", hours_offset=idx)
        for idx in range(3)
    ]
    seed_event(db_session, readings[0])
    seed_event(db_session, readings[1], event_type="comfort_divergence")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "readings_stored": 3,
        "events_stored": 2,
    }


def test_readings_filtered_by_city(client, db_session: Session) -> None:
    for idx in range(5):
        seed_reading(db_session, city="Toronto", hours_offset=idx)
    for idx in range(3):
        seed_reading(db_session, city="Ottawa", hours_offset=idx + 10)

    response = client.get("/readings?city=Toronto&limit=10")

    assert response.status_code == 200
    readings = response.json()["readings"]
    assert len(readings) == 5
    assert all(reading["city"] == "Toronto" for reading in readings)


def test_readings_filtered_by_explicit_range(client, db_session: Session) -> None:
    for idx in range(5):
        seed_reading(db_session, city="Toronto", hours_offset=idx)

    start = (BASE_TS.replace(hour=13)).isoformat()
    end = (BASE_TS.replace(hour=15)).isoformat()
    response = client.get(
        "/readings",
        params={"city": "Toronto", "start": start, "end": end, "limit": 10},
    )

    assert response.status_code == 200
    readings = response.json()["readings"]
    assert [reading["observation_ts"] for reading in readings] == [
        "2026-05-27T15:00:00Z",
        "2026-05-27T14:00:00Z",
        "2026-05-27T13:00:00Z",
    ]


def test_readings_without_range_params_keep_existing_behavior(
    client, db_session: Session
) -> None:
    for idx in range(4):
        seed_reading(db_session, city="Toronto", hours_offset=idx)

    response = client.get("/readings?city=Toronto&limit=3")

    assert response.status_code == 200
    readings = response.json()["readings"]
    assert list(readings[0]) == [
        "id",
        "city",
        "observation_ts",
        "polled_at",
        "temperature_2m",
        "apparent_temperature",
        "precipitation",
        "wind_speed_10m",
        "weather_code",
    ]
    assert [reading["observation_ts"] for reading in readings] == [
        "2026-05-27T15:00:00Z",
        "2026-05-27T14:00:00Z",
        "2026-05-27T13:00:00Z",
    ]


def test_events_filtered_by_city(client, db_session: Session) -> None:
    toronto = seed_reading(db_session, city="Toronto", hours_offset=0)
    ottawa = seed_reading(db_session, city="Ottawa", hours_offset=1)
    seed_event(db_session, toronto)
    seed_event(db_session, ottawa, event_type="wmo_transition")

    response = client.get("/events?city=Ottawa&limit=10")

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["city"] == "Ottawa"
    assert events[0]["event_type"] == "wmo_transition"


def test_events_filtered_by_explicit_range(client, db_session: Session) -> None:
    readings = [
        seed_reading(db_session, city="Toronto", hours_offset=idx)
        for idx in range(5)
    ]
    for reading in readings:
        seed_event(db_session, reading)

    start = (BASE_TS.replace(hour=13)).isoformat()
    end = (BASE_TS.replace(hour=15)).isoformat()
    response = client.get(
        "/events",
        params={"city": "Toronto", "start": start, "end": end, "limit": 10},
    )

    assert response.status_code == 200
    events = response.json()["events"]
    assert [event["event_ts"] for event in events] == [
        "2026-05-27T15:00:00Z",
        "2026-05-27T14:00:00Z",
        "2026-05-27T13:00:00Z",
    ]


def test_events_without_range_params_keep_existing_behavior(client, db_session: Session) -> None:
    readings = [
        seed_reading(db_session, city="Toronto", hours_offset=idx)
        for idx in range(4)
    ]
    for reading in readings:
        seed_event(db_session, reading)

    response = client.get("/events?city=Toronto&limit=3")

    assert response.status_code == 200
    events = response.json()["events"]
    assert list(events[0]) == [
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
    ]
    assert [event["event_ts"] for event in events] == [
        "2026-05-27T15:00:00Z",
        "2026-05-27T14:00:00Z",
        "2026-05-27T13:00:00Z",
    ]


def test_limit_validation(client) -> None:
    response = client.get("/readings?limit=501")

    assert response.status_code == 422


def test_forecasts_returns_documented_shape(client, db_session: Session) -> None:
    seed_forecast(db_session, city="Toronto", hours_offset=1, temperature_2m=21.5)

    response = client.get("/forecasts?limit=10")

    assert response.status_code == 200
    assert response.json() == {
        "forecasts": [
            {
                "city": "Toronto",
                "target_ts": "2026-05-27T13:00:00Z",
                "issued_at": "2026-05-27T06:00:00Z",
                "lead_hours": 6,
                "temperature_2m": 21.5,
                "precipitation": 0.0,
                "wind_speed_10m": 10.0,
                "weather_code": 0,
            }
        ]
    }


def test_forecasts_filtered_by_city(client, db_session: Session) -> None:
    seed_forecast(db_session, city="Toronto", hours_offset=1)
    seed_forecast(db_session, city="Ottawa", hours_offset=2)

    response = client.get("/forecasts?city=Ottawa&limit=10")

    assert response.status_code == 200
    forecasts = response.json()["forecasts"]
    assert len(forecasts) == 1
    assert forecasts[0]["city"] == "Ottawa"


def test_forecasts_filtered_by_explicit_range(client, db_session: Session) -> None:
    for idx in range(5):
        seed_forecast(db_session, city="Toronto", hours_offset=idx)

    start = (BASE_TS.replace(hour=13)).isoformat()
    end = (BASE_TS.replace(hour=15)).isoformat()
    response = client.get(
        "/forecasts",
        params={"city": "Toronto", "start": start, "end": end, "limit": 10},
    )

    assert response.status_code == 200
    forecasts = response.json()["forecasts"]
    assert [forecast["target_ts"] for forecast in forecasts] == [
        "2026-05-27T15:00:00Z",
        "2026-05-27T14:00:00Z",
        "2026-05-27T13:00:00Z",
    ]


def test_forecasts_limit_validation(client) -> None:
    response = client.get("/forecasts?limit=501")

    assert response.status_code == 422


def test_forecasts_reject_naive_datetime(client) -> None:
    response = client.get("/forecasts?start=2026-05-27T13:00:00")

    assert response.status_code == 422
    assert response.json()["detail"] == "start must be timezone-aware"
