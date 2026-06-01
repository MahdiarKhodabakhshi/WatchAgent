from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.detection.base import EventCandidate as DetectionEvent
from app.detection.lifecycle import apply_lifecycle
from app.detection.scoring import candidate_priority_score, severity_from_score
from app.models import Event, Forecast, Reading
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
    observed_reading: Reading | None = None,
    created_at: datetime | None = None,
) -> list[Event]:
    now = created_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")

    if observed_reading is not None:
        return apply_lifecycle(
            session,
            events,
            observed_reading=observed_reading,
            created_at=now,
        )

    rows: list[Event] = []
    for event in events:
        score = candidate_priority_score(event)
        rows.append(
            Event(
                city=event.city,
                event_ts=event.event_ts,
                created_at=now.astimezone(timezone.utc),
                event_type=event.event_type,
                severity=severity_from_score(score),
                metric=event.metric,
                signal_values=event.signal_values,
                reason=event.reason,
                supporting_reading_ids=event.supporting_reading_ids,
                status="open",
                onset_ts=event.event_ts,
                peak_ts=event.event_ts,
                resolved_ts=None,
                priority_score=score,
                confidence=None,
                rarity_percentile=None,
                detector_name=event.event_type,
                detector_version="legacy-direct-v1",
                dedupe_key=None,
                related_event_ids=[],
                evidence=dict(event.signal_values),
            )
        )
    session.add_all(rows)
    session.flush()
    return rows


def store_forecast_if_new(
    session: Session,
    forecast_data: Mapping[str, Any],
) -> Forecast | None:
    """Store a forecast row, keeping the earliest lead for each (city, target_ts)."""
    existing = session.scalar(
        select(Forecast).where(
            Forecast.city == forecast_data["city"],
            Forecast.target_ts == forecast_data["target_ts"],
        )
    )
    if existing is not None:
        return None

    forecast = Forecast(**dict(forecast_data))
    session.add(forecast)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return None
    return forecast


def matching_forecast(
    session: Session,
    city: str,
    target_ts: datetime,
    min_lead: int,
    max_lead: int,
) -> Forecast | None:
    """Return the stored forecast for (city, target_ts) if its lead is within bounds."""
    return session.scalar(
        select(Forecast).where(
            Forecast.city == city,
            Forecast.target_ts == target_ts,
            Forecast.lead_hours >= min_lead,
            Forecast.lead_hours <= max_lead,
        )
    )


def forecast_comparison_pairs(
    session: Session,
    before: datetime,
    *,
    hours: int = 14 * 24,
    limit: int = 200,
) -> tuple[tuple[Reading, Forecast], ...]:
    """Return recent obs/forecast pairs for global rolling MAE by metric."""
    if before.tzinfo is None:
        raise ValueError("before must be timezone-aware")
    cutoff = before.astimezone(timezone.utc) - timedelta(hours=hours)
    rows = session.execute(
        select(Reading, Forecast)
        .join(
            Forecast,
            (Forecast.city == Reading.city)
            & (Forecast.target_ts == Reading.observation_ts),
        )
        .where(Reading.observation_ts < before.astimezone(timezone.utc))
        .where(Reading.observation_ts >= cutoff)
        .order_by(Reading.observation_ts.desc())
        .limit(limit)
    ).all()
    return tuple((reading, forecast) for reading, forecast in rows)


def count_readings(session: Session) -> int:
    return int(session.scalar(select(func.count(Reading.id))) or 0)


def count_events(session: Session) -> int:
    return int(session.scalar(select(func.count(Event.id))) or 0)
