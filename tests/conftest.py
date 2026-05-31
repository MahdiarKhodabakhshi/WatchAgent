from __future__ import annotations

import os
from collections.abc import Callable, Generator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("ENABLE_POLLER", "false")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Event, Forecast, Reading  # noqa: E402

BASE_TS = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with SessionTesting() as session:
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def reading_factory() -> Callable[..., Reading]:
    def _make(
        *,
        id: int | None = None,
        city: str = "Toronto",
        hours_offset: int = 0,
        temperature_2m: float = 20.0,
        apparent_temperature: float | None = None,
        precipitation: float = 0.0,
        wind_speed_10m: float = 10.0,
        weather_code: int = 0,
    ) -> Reading:
        return Reading(
            id=id,
            city=city,
            observation_ts=BASE_TS + timedelta(hours=hours_offset),
            polled_at=BASE_TS + timedelta(hours=hours_offset, minutes=5),
            temperature_2m=temperature_2m,
            apparent_temperature=(
                temperature_2m if apparent_temperature is None else apparent_temperature
            ),
            precipitation=precipitation,
            wind_speed_10m=wind_speed_10m,
            weather_code=weather_code,
        )

    return _make


def seed_reading(
    session: Session,
    *,
    city: str = "Toronto",
    hours_offset: int = 0,
    temperature_2m: float = 20.0,
    apparent_temperature: float | None = None,
    precipitation: float = 0.0,
    wind_speed_10m: float = 10.0,
    weather_code: int = 0,
) -> Reading:
    reading = Reading(
        city=city,
        observation_ts=BASE_TS + timedelta(hours=hours_offset),
        polled_at=BASE_TS + timedelta(hours=hours_offset, minutes=5),
        temperature_2m=temperature_2m,
        apparent_temperature=(
            temperature_2m if apparent_temperature is None else apparent_temperature
        ),
        precipitation=precipitation,
        wind_speed_10m=wind_speed_10m,
        weather_code=weather_code,
    )
    session.add(reading)
    session.commit()
    return reading


def seed_event(session: Session, reading: Reading, event_type: str = "rapid_change") -> Event:
    event = Event(
        city=reading.city,
        event_ts=reading.observation_ts,
        created_at=reading.polled_at,
        event_type=event_type,
        severity="warning",
        metric="temperature_2m",
        signal_values={"value": reading.temperature_2m, "z_score": 3.0},
        reason="Temperature 26.0 is 3.0 sigma from Toronto's recent mean.",
        supporting_reading_ids=[reading.id],
    )
    session.add(event)
    session.commit()
    return event


def seed_forecast(
    session: Session,
    *,
    city: str = "Toronto",
    hours_offset: int = 0,
    issued_offset: int = -6,
    lead_hours: int = 6,
    temperature_2m: float = 20.0,
    precipitation: float = 0.0,
    wind_speed_10m: float = 10.0,
    weather_code: int = 0,
) -> Forecast:
    forecast = Forecast(
        city=city,
        target_ts=BASE_TS + timedelta(hours=hours_offset),
        issued_at=BASE_TS + timedelta(hours=issued_offset),
        lead_hours=lead_hours,
        temperature_2m=temperature_2m,
        precipitation=precipitation,
        wind_speed_10m=wind_speed_10m,
        weather_code=weather_code,
    )
    session.add(forecast)
    session.commit()
    return forecast
