from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Reading
from app.storage import store_reading_if_new


def test_dedup_same_city_and_timestamp_only_stored_once(db_session: Session) -> None:
    data = {
        "city": "Ottawa",
        "observation_ts": datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc),
        "polled_at": datetime(2026, 5, 27, 18, 5, tzinfo=timezone.utc),
        "temperature_2m": 21.5,
        "apparent_temperature": 20.9,
        "precipitation": 0.0,
        "wind_speed_10m": 11.0,
        "weather_code": 1,
    }

    first = store_reading_if_new(db_session, data)
    second = store_reading_if_new(db_session, data)
    db_session.commit()

    rows = db_session.scalars(select(Reading).where(Reading.city == "Ottawa")).all()
    assert first is not None
    assert second is None
    assert len(rows) == 1


def test_dedup_allows_same_timestamp_for_different_cities(db_session: Session) -> None:
    ts = datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc)
    base = {
        "observation_ts": ts,
        "polled_at": datetime(2026, 5, 27, 18, 5, tzinfo=timezone.utc),
        "temperature_2m": 21.5,
        "apparent_temperature": 20.9,
        "precipitation": 0.0,
        "wind_speed_10m": 11.0,
        "weather_code": 1,
    }

    assert store_reading_if_new(db_session, {"city": "Ottawa", **base}) is not None
    assert store_reading_if_new(db_session, {"city": "Toronto", **base}) is not None
    db_session.commit()

    assert len(db_session.scalars(select(Reading)).all()) == 2
