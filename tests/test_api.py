from sqlalchemy.orm import Session

from tests.conftest import seed_event, seed_reading


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


def test_limit_validation(client) -> None:
    response = client.get("/readings?limit=501")

    assert response.status_code == 422
