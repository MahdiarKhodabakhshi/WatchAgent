"""Build a deterministic in-memory (or file-backed) SQLite database for eval.

Every value is hard-coded so expected answers are exact and reproducible.
The seed window spans 2026-01-01 to 2026-01-10 (10 days).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import Event as EventRow
from app.models import Reading

SEED_START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

CITIES = ["Ottawa", "Toronto", "Vancouver"]

_TEMP_PROFILES = {
    "Ottawa": [
        -8.0, -7.5, -9.0, -6.0, -5.5, -7.0, -8.5, -6.5, -5.0, -7.0,
    ],
    "Toronto": [
        2.0, 3.5, 1.0, 4.0, 5.5, 3.0, 2.5, 6.0, 4.5, 3.5,
    ],
    "Vancouver": [
        8.0, 9.5, 10.0, 11.2, 12.0, 9.0, 8.5, 11.5, 12.8, 10.0,
    ],
}

_WIND_PROFILES = {
    "Ottawa": [12.0, 14.0, 11.0, 15.0, 13.0, 10.0, 16.0, 12.0, 14.0, 11.0],
    "Toronto": [8.0, 7.0, 9.0, 6.0, 8.5, 7.5, 10.0, 6.5, 9.0, 7.0],
    "Vancouver": [5.0, 4.0, 6.0, 3.5, 5.5, 4.5, 3.0, 6.5, 5.0, 4.0],
}

_PRECIP_PROFILES = {
    "Ottawa": [0.0, 0.5, 0.0, 1.2, 0.0, 0.0, 0.8, 0.0, 0.3, 0.0],
    "Toronto": [0.0, 0.0, 0.2, 0.0, 1.5, 0.0, 0.0, 0.3, 0.0, 0.0],
    "Vancouver": [1.0, 2.0, 0.5, 0.0, 3.0, 1.5, 0.0, 2.5, 0.5, 1.0],
}

_EVENTS_SPEC: list[dict] = [
    # rapid_change: 4 Toronto, 2 Ottawa, 1 Vancouver = 7 total
    {"city": "Toronto", "day": 1, "type": "rapid_change", "severity": "warning",
     "metric": "temperature_2m"},
    {"city": "Toronto", "day": 3, "type": "rapid_change", "severity": "severe",
     "metric": "temperature_2m"},
    {"city": "Toronto", "day": 5, "type": "rapid_change", "severity": "warning",
     "metric": "wind_speed_10m"},
    {"city": "Toronto", "day": 8, "type": "rapid_change", "severity": "warning",
     "metric": "temperature_2m"},
    {"city": "Ottawa", "day": 2, "type": "rapid_change", "severity": "warning",
     "metric": "wind_speed_10m"},
    {"city": "Ottawa", "day": 6, "type": "rapid_change", "severity": "severe",
     "metric": "temperature_2m"},
    {"city": "Vancouver", "day": 4, "type": "rapid_change", "severity": "warning",
     "metric": "precipitation"},
    # wmo_transition: 3 total
    {"city": "Ottawa", "day": 3, "type": "wmo_transition", "severity": "warning",
     "metric": "weather_code"},
    {"city": "Toronto", "day": 7, "type": "wmo_transition", "severity": "severe",
     "metric": "weather_code"},
    {"city": "Vancouver", "day": 9, "type": "wmo_transition", "severity": "warning",
     "metric": "weather_code"},
    # comfort_divergence: 2 total
    {"city": "Ottawa", "day": 5, "type": "comfort_divergence", "severity": "warning",
     "metric": "apparent_temperature"},
    {"city": "Toronto", "day": 9, "type": "comfort_divergence", "severity": "warning",
     "metric": "apparent_temperature"},
    # sustained_extreme: 2 total
    {"city": "Vancouver", "day": 6, "type": "sustained_extreme", "severity": "warning",
     "metric": "precipitation"},
    {"city": "Ottawa", "day": 8, "type": "sustained_extreme", "severity": "severe",
     "metric": "wind_speed_10m"},
]
# Total events: 14  |  severe: 3 (Toronto rapid d3, Ottawa rapid d6, Ottawa sustained d8)


def build_seed_db(database_url: str = "sqlite://") -> sessionmaker[Session]:
    """Create and populate a seed database. Returns a session factory."""
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        _insert_readings(session)
        _insert_events(session)
        session.commit()

    return factory


def _insert_readings(session: Session) -> None:
    rid = 1
    for city in CITIES:
        temps = _TEMP_PROFILES[city]
        winds = _WIND_PROFILES[city]
        precips = _PRECIP_PROFILES[city]
        for day_idx in range(10):
            ts = SEED_START + timedelta(days=day_idx, hours=12)
            session.add(
                Reading(
                    id=rid,
                    city=city,
                    observation_ts=ts,
                    polled_at=ts + timedelta(minutes=5),
                    temperature_2m=temps[day_idx],
                    apparent_temperature=temps[day_idx] - 2.0,
                    precipitation=precips[day_idx],
                    wind_speed_10m=winds[day_idx],
                    weather_code=0,
                )
            )
            rid += 1
    session.flush()


def _insert_events(session: Session) -> None:
    eid = 1
    for spec in _EVENTS_SPEC:
        ts = SEED_START + timedelta(days=spec["day"], hours=12)
        session.add(
            EventRow(
                id=eid,
                city=spec["city"],
                event_ts=ts,
                created_at=ts + timedelta(minutes=10),
                event_type=spec["type"],
                severity=spec["severity"],
                metric=spec["metric"],
                signal_values={"seeded": True},
                reason=f"Seed event {eid}: {spec['type']} in {spec['city']}.",
                supporting_reading_ids=[],
            )
        )
        eid += 1
    session.flush()
