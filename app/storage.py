from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.detection.base import Event as DetectionEvent
from app.models import Event, Reading
from app.open_meteo import CITY_NAMES


def store_reading_if_new(
    session: Session,
    reading_data: Mapping[str, Any],
) -> Reading | None:
    existing = session.scalar(
        select(Reading).where(
            Reading.city == reading_data["city"],
            Reading.observation_ts == reading_data["observation_ts"],
        )
    )
    if existing is not None:
        return None

    reading = Reading(**dict(reading_data))
    session.add(reading)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return None
    return reading


def recent_history(
    session: Session,
    city: str,
    *,
    before: datetime | None = None,
    hours: int = 48,
    limit: int | None = None,
) -> list[Reading]:
    anchor = before or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        raise ValueError("before must be timezone-aware")
    cutoff = anchor.astimezone(timezone.utc) - timedelta(hours=hours)

    query = (
        select(Reading)
        .where(Reading.city == city)
        .where(Reading.observation_ts >= cutoff)
        .order_by(Reading.observation_ts.desc())
    )
    if before is not None:
        query = query.where(Reading.observation_ts < before.astimezone(timezone.utc))
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query).all())


def latest_peer_readings(
    session: Session,
    *,
    exclude_city: str,
    at_or_before: datetime,
) -> dict[str, Reading]:
    if at_or_before.tzinfo is None:
        raise ValueError("at_or_before must be timezone-aware")

    peers: dict[str, Reading] = {}
    for city in CITY_NAMES:
        if city == exclude_city:
            continue
        peer = session.scalar(
            select(Reading)
            .where(Reading.city == city)
            .where(Reading.observation_ts <= at_or_before.astimezone(timezone.utc))
            .order_by(Reading.observation_ts.desc())
            .limit(1)
        )
        if peer is not None:
            peers[city] = peer
    return peers


def store_events(
    session: Session,
    events: Iterable[DetectionEvent],
    *,
    created_at: datetime | None = None,
) -> list[Event]:
    now = created_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")

    rows = [
        Event(
            city=event.city,
            event_ts=event.event_ts,
            created_at=now.astimezone(timezone.utc),
            event_type=event.event_type,
            severity=event.severity,
            metric=event.metric,
            signal_values=event.signal_values,
            reason=event.reason,
            supporting_reading_ids=event.supporting_reading_ids,
        )
        for event in events
    ]
    session.add_all(rows)
    session.flush()
    return rows


def count_readings(session: Session) -> int:
    return int(session.scalar(select(func.count(Reading.id))) or 0)


def count_events(session: Session) -> int:
    return int(session.scalar(select(func.count(Event.id))) or 0)
