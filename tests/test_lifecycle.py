from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.detection.base import EventCandidate
from app.detection.lifecycle import LifecycleConfig, LifecycleManager, apply_lifecycle
from app.models import Event

BASE_TS = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
CONFIG = LifecycleConfig(k_on=1, k_off=2, z_on=2.5, z_off=1.5)


def test_lifecycle_resolves_high_then_back_to_normal(db_session: Session) -> None:
    high = _reading(0)
    normal_1 = _reading(1)
    normal_2 = _reading(2)

    apply_lifecycle(db_session, [_candidate(high, z=3.0)], observed_reading=high, config=CONFIG)
    apply_lifecycle(db_session, [], observed_reading=normal_1, config=CONFIG)
    apply_lifecycle(db_session, [], observed_reading=normal_2, config=CONFIG)
    db_session.commit()

    events = _events(db_session)
    assert len(events) == 1
    assert events[0].status == "resolved"
    assert events[0].resolved_ts == normal_2.observation_ts


def test_lifecycle_hysteresis_prevents_flapping(db_session: Session) -> None:
    for offset, candidates in [
        (0, [_candidate(_reading(0), z=3.0)]),
        (1, []),
        (2, [_candidate(_reading(2), z=3.1)]),
        (3, []),
        (4, [_candidate(_reading(4), z=3.2)]),
    ]:
        reading = _reading(offset)
        apply_lifecycle(db_session, candidates, observed_reading=reading, config=CONFIG)
    db_session.commit()

    events = _events(db_session)
    assert len(events) == 1
    assert events[0].status == "ongoing"
    assert events[0].dedupe_key == "Toronto|rapid_change|temperature_2m"


def test_lifecycle_peak_ts_tracks_middle_peak(db_session: Session) -> None:
    for offset, z in [(0, 3.0), (1, 5.0), (2, 4.0)]:
        reading = _reading(offset)
        apply_lifecycle(
            db_session,
            [_candidate(reading, z=z)],
            observed_reading=reading,
            config=CONFIG,
        )
    db_session.commit()

    event = _events(db_session)[0]
    assert event.peak_ts == _reading(1).observation_ts
    assert event.signal_values["z_score"] == 5.0
    assert event.evidence["lifecycle"]["peak_strength"] == 5.0


def test_lifecycle_onset_ts_is_stable_across_updates(db_session: Session) -> None:
    config = LifecycleConfig(k_on=2, k_off=2, z_on=2.5, z_off=1.5)
    for offset, z in [(0, 3.0), (1, 3.5), (2, 4.0)]:
        reading = _reading(offset)
        apply_lifecycle(
            db_session,
            [_candidate(reading, z=z)],
            observed_reading=reading,
            config=config,
        )
    db_session.commit()

    event = _events(db_session)[0]
    assert event.onset_ts == _reading(0).observation_ts
    assert event.status == "ongoing"


def test_lifecycle_restart_safety_continues_open_incident(db_session: Session) -> None:
    first_manager = LifecycleManager(CONFIG)
    second_manager = LifecycleManager(CONFIG)

    first_manager.apply(
        db_session,
        [_candidate(_reading(0), z=3.0)],
        observed_reading=_reading(0),
    )
    db_session.commit()

    second_manager.apply(
        db_session,
        [_candidate(_reading(1), z=4.0)],
        observed_reading=_reading(1),
    )
    second_manager.apply(db_session, [], observed_reading=_reading(2))
    second_manager.apply(db_session, [], observed_reading=_reading(3))
    db_session.commit()

    events = _events(db_session)
    assert len(events) == 1
    assert events[0].status == "resolved"
    assert events[0].resolved_ts == _reading(3).observation_ts
    assert events[0].supporting_reading_ids == [1, 2]


def test_lifecycle_concurrent_incidents_resolve_independently(db_session: Session) -> None:
    apply_lifecycle(
        db_session,
        [
            _candidate(_reading(0, id=1), z=3.0, metric="temperature_2m"),
            _candidate(
                _reading(0, id=2),
                z=4.0,
                event_type="sustained_extreme",
                metric="wind_speed_10m",
            ),
        ],
        observed_reading=_reading(0),
        config=CONFIG,
    )
    for offset in (1, 2):
        reading = _reading(offset)
        apply_lifecycle(
            db_session,
            [_candidate(reading, z=3.0, metric="temperature_2m")],
            observed_reading=reading,
            config=CONFIG,
        )
    for offset in (3, 4):
        apply_lifecycle(db_session, [], observed_reading=_reading(offset), config=CONFIG)
    db_session.commit()

    events = sorted(_events(db_session), key=lambda event: event.event_type)
    assert len(events) == 2
    assert events[0].event_type == "rapid_change"
    assert events[0].status == "resolved"
    assert events[0].resolved_ts == _reading(4).observation_ts
    assert events[1].event_type == "sustained_extreme"
    assert events[1].status == "resolved"
    assert events[1].resolved_ts == _reading(2).observation_ts


def test_lifecycle_cold_start_without_candidates_opens_no_incident(db_session: Session) -> None:
    touched = apply_lifecycle(db_session, [], observed_reading=_reading(0), config=CONFIG)
    db_session.commit()

    assert touched == []
    assert _events(db_session) == []


def test_events_endpoint_sorts_by_priority_score(client, db_session: Session) -> None:
    low = _event_row(_reading(0), priority_score=20.0, event_type="fun_fact")
    high = _event_row(_reading(1), priority_score=70.0, event_type="rapid_change")
    db_session.add_all([low, high])
    db_session.commit()

    response = client.get("/events?city=Toronto&limit=10")

    assert response.status_code == 200
    events = response.json()["events"]
    assert [event["priority_score"] for event in events] == [70.0, 20.0]
    assert [event["event_type"] for event in events] == ["rapid_change", "fun_fact"]


def _candidate(
    reading: SimpleNamespace,
    *,
    z: float,
    event_type: str = "rapid_change",
    metric: str = "temperature_2m",
) -> EventCandidate:
    return EventCandidate(
        city=reading.city,
        event_ts=reading.observation_ts,
        event_type=event_type,
        severity="warning",
        metric=metric,
        signal_values={"z_score": z},
        reason=f"{metric} is {z:.1f} sigma from baseline.",
        supporting_reading_ids=[reading.id],
    )


def _reading(hours_offset: int, *, id: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=id if id is not None else hours_offset + 1,
        city="Toronto",
        observation_ts=BASE_TS + timedelta(hours=hours_offset),
    )


def _events(session: Session) -> list[Event]:
    return list(session.scalars(select(Event).order_by(Event.id)).all())


def _event_row(
    reading: SimpleNamespace,
    *,
    priority_score: float,
    event_type: str,
) -> Event:
    return Event(
        city=reading.city,
        event_ts=reading.observation_ts,
        created_at=reading.observation_ts,
        event_type=event_type,
        severity="severe" if priority_score >= 60 else "info",
        metric="temperature_2m",
        signal_values={"value": priority_score},
        reason="Seeded lifecycle event.",
        supporting_reading_ids=[reading.id],
        status="open",
        onset_ts=reading.observation_ts,
        peak_ts=reading.observation_ts,
        resolved_ts=None,
        priority_score=priority_score,
        confidence=1.0,
        rarity_percentile=None,
        detector_name=event_type,
        detector_version="test",
        dedupe_key=f"Toronto|{event_type}|temperature_2m",
        related_event_ids=[],
        evidence={},
    )
