from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.detection.base import EventCandidate
from app.detection.explain import explain_candidate
from app.detection.scoring import candidate_priority_score, severity_from_score
from app.models import Event, IncidentState

ACTIVE_STATUSES = ("open", "ongoing")
LEGACY_STRENGTH_BY_SEVERITY = {
    "info": 1.0,
    "warning": 2.0,
    "severe": 3.0,
}


@dataclass(frozen=True)
class LifecycleConfig:
    k_on: int = 1
    k_off: int = 2
    z_on: float = 1.0
    z_off: float = 0.5
    detector_version: str = "legacy-adapter-v1"


class LifecycleManager:
    def __init__(self, config: LifecycleConfig | None = None) -> None:
        self.config = config or LifecycleConfig()

    def apply(
        self,
        session: Session,
        candidates: Iterable[EventCandidate],
        *,
        observed_reading: Any,
        created_at: datetime | None = None,
    ) -> list[Event]:
        return apply_lifecycle(
            session,
            candidates,
            observed_reading=observed_reading,
            created_at=created_at,
            config=self.config,
        )


def apply_lifecycle(
    session: Session,
    candidates: Iterable[EventCandidate],
    *,
    observed_reading: Any,
    created_at: datetime | None = None,
    config: LifecycleConfig | None = None,
) -> list[Event]:
    resolved_config = config or LifecycleConfig()
    now = created_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")

    candidate_by_key = _highest_priority_candidates(candidates)
    touched: list[Event] = []
    for key, candidate in candidate_by_key.items():
        event = _apply_candidate(session, key, candidate, now, resolved_config)
        if event is not None:
            touched.append(event)

    touched.extend(
        _clear_absent_incidents(
            session,
            observed_reading=observed_reading,
            firing_keys=set(candidate_by_key),
            config=resolved_config,
        )
    )
    session.flush()
    return touched


def dedupe_key_for_candidate(candidate: EventCandidate) -> str:
    if candidate.dedupe_key:
        return candidate.dedupe_key
    parts = [
        candidate.city,
        candidate.event_type,
        candidate.metric or "none",
    ]
    if candidate.event_type == "cross_city_contrast":
        peer_city = candidate.signal_values.get("peer_city")
        if peer_city is not None:
            parts.append(str(peer_city))
    if candidate.event_type == "fun_fact":
        kind = candidate.signal_values.get("kind")
        if kind is not None:
            parts.append(str(kind))
    return "|".join(parts)


def _apply_candidate(
    session: Session,
    key: str,
    candidate: EventCandidate,
    created_at: datetime,
    config: LifecycleConfig,
) -> Event | None:
    state = _state_for_candidate(session, key, candidate)
    active = _open_event(session, key)
    strength = _candidate_strength(candidate)
    score = candidate_priority_score(candidate)

    if active is not None:
        state.state = "active"
        state.active_event_id = active.id
        if strength < config.z_off:
            return _clear_active_event(session, state, active, candidate.event_ts, config)
        state.clear_count = 0
        return _update_active_event(active, candidate, strength, score, config)

    if strength < config.z_on:
        _reset_pending_state(state, candidate.event_ts)
        return None

    _record_pending_candidate(state, candidate, strength, score)
    if state.enter_count < config.k_on:
        return None

    event = _open_new_event(session, state, candidate, created_at, config)
    state.state = "active"
    state.active_event_id = event.id
    state.enter_count = config.k_on
    state.clear_count = 0
    return event


def _clear_absent_incidents(
    session: Session,
    *,
    observed_reading: Any,
    firing_keys: set[str],
    config: LifecycleConfig,
) -> list[Event]:
    city = observed_reading.city
    ts = observed_reading.observation_ts
    resolved: list[Event] = []

    active_events = session.scalars(
        select(Event)
        .where(Event.city == city)
        .where(Event.status.in_(ACTIVE_STATUSES))
    ).all()
    for event in active_events:
        if event.dedupe_key in firing_keys:
            continue
        state = _state_for_event(session, event)
        maybe_resolved = _clear_active_event(session, state, event, ts, config)
        if maybe_resolved is not None:
            resolved.append(maybe_resolved)

    pending_states = session.scalars(
        select(IncidentState)
        .where(IncidentState.city == city)
        .where(IncidentState.state == "pending")
    ).all()
    for state in pending_states:
        if state.dedupe_key in firing_keys:
            continue
        _reset_pending_state(state, ts)

    return resolved


def _open_new_event(
    session: Session,
    state: IncidentState,
    candidate: EventCandidate,
    created_at: datetime,
    config: LifecycleConfig,
) -> Event:
    pending = state.state_values
    score = float(pending.get("pending_priority_score", candidate_priority_score(candidate)))
    explanation = explain_candidate(candidate)
    onset_ts = state.first_seen_ts or candidate.event_ts
    peak_ts = datetime.fromisoformat(str(pending["pending_peak_ts"]))
    if peak_ts.tzinfo is None:
        peak_ts = peak_ts.replace(tzinfo=timezone.utc)

    event = Event(
        city=candidate.city,
        event_ts=onset_ts,
        created_at=created_at.astimezone(timezone.utc),
        event_type=candidate.event_type,
        severity=severity_from_score(score),
        metric=candidate.metric,
        signal_values=dict(pending.get("pending_signal_values", candidate.signal_values)),
        reason=str(pending.get("pending_reason", explanation.headline)),
        supporting_reading_ids=list(pending.get("pending_supporting_reading_ids", [])),
        status="open",
        onset_ts=onset_ts,
        peak_ts=peak_ts,
        resolved_ts=None,
        priority_score=score,
        confidence=_candidate_confidence(candidate),
        rarity_percentile=None,
        detector_name=candidate.detector_name or candidate.event_type,
        detector_version=candidate.detector_version or config.detector_version,
        dedupe_key=state.dedupe_key,
        related_event_ids=[],
        evidence=_event_evidence(
            candidate,
            peak_strength=float(pending.get("pending_peak_strength", 0.0)),
            clear_count=0,
        ),
    )
    session.add(event)
    session.flush()
    return event


def _update_active_event(
    event: Event,
    candidate: EventCandidate,
    strength: float,
    score: float,
    config: LifecycleConfig,
) -> Event:
    evidence = dict(event.evidence or {})
    previous_peak = float(evidence.get("lifecycle", {}).get("peak_strength", 0.0))

    event.status = "ongoing"
    event.resolved_ts = None
    event.priority_score = max(float(event.priority_score or 0.0), score)
    event.severity = severity_from_score(float(event.priority_score))
    event.confidence = max(float(event.confidence or 0.0), _candidate_confidence(candidate))
    event.supporting_reading_ids = _merged_ids(
        event.supporting_reading_ids,
        candidate.supporting_reading_ids,
    )
    event.detector_version = event.detector_version or config.detector_version

    if strength > previous_peak:
        event.peak_ts = candidate.event_ts
        event.signal_values = dict(candidate.signal_values)
        event.reason = candidate.reason
        evidence = _event_evidence(candidate, peak_strength=strength, clear_count=0)
    else:
        evidence["lifecycle"] = {
            **dict(evidence.get("lifecycle", {})),
            "clear_count": 0,
            "last_candidate_ts": candidate.event_ts.isoformat(),
        }
    event.evidence = evidence
    return event


def _clear_active_event(
    session: Session,
    state: IncidentState,
    event: Event,
    observed_ts: datetime,
    config: LifecycleConfig,
) -> Event | None:
    state.clear_count += 1
    state.last_seen_ts = observed_ts
    event.evidence = {
        **dict(event.evidence or {}),
        "lifecycle": {
            **dict((event.evidence or {}).get("lifecycle", {})),
            "clear_count": state.clear_count,
        },
    }
    if state.clear_count < config.k_off:
        return None

    event.status = "resolved"
    event.resolved_ts = observed_ts
    event.severity = severity_from_score(float(event.priority_score or 0.0))
    state.state = "resolved"
    state.active_event_id = event.id
    session.flush()
    return event


def _record_pending_candidate(
    state: IncidentState,
    candidate: EventCandidate,
    strength: float,
    score: float,
) -> None:
    if state.state != "pending":
        state.enter_count = 0
        state.clear_count = 0
        state.first_seen_ts = None
        state.state_values = {}
    state.state = "pending"
    state.enter_count += 1
    state.clear_count = 0
    state.last_seen_ts = candidate.event_ts
    if state.enter_count == 1 or state.first_seen_ts is None:
        state.first_seen_ts = candidate.event_ts

    pending = dict(state.state_values or {})
    previous_peak = float(pending.get("pending_peak_strength", 0.0))
    if strength >= previous_peak:
        pending["pending_peak_strength"] = strength
        pending["pending_peak_ts"] = candidate.event_ts.isoformat()
        pending["pending_signal_values"] = dict(candidate.signal_values)
        pending["pending_reason"] = candidate.reason
        pending["pending_priority_score"] = score
        pending["pending_evidence"] = dict(candidate.evidence or candidate.signal_values)
    pending["pending_supporting_reading_ids"] = _merged_ids(
        pending.get("pending_supporting_reading_ids", []),
        candidate.supporting_reading_ids,
    )
    state.state_values = pending


def _reset_pending_state(state: IncidentState, ts: datetime) -> None:
    state.state = "idle"
    state.enter_count = 0
    state.clear_count = 0
    state.first_seen_ts = None
    state.last_seen_ts = ts
    state.state_values = {}


def _state_for_candidate(
    session: Session,
    key: str,
    candidate: EventCandidate,
) -> IncidentState:
    state = session.get(IncidentState, key)
    if state is not None:
        return state
    state = IncidentState(
        dedupe_key=key,
        city=candidate.city,
        event_type=candidate.event_type,
        metric=candidate.metric,
        state="idle",
        enter_count=0,
        clear_count=0,
        first_seen_ts=None,
        last_seen_ts=None,
        active_event_id=None,
        state_values={},
    )
    session.add(state)
    session.flush()
    return state


def _state_for_event(session: Session, event: Event) -> IncidentState:
    if event.dedupe_key is None:
        raise ValueError("active lifecycle event missing dedupe_key")
    state = session.get(IncidentState, event.dedupe_key)
    if state is not None:
        return state
    state = IncidentState(
        dedupe_key=event.dedupe_key,
        city=event.city,
        event_type=event.event_type,
        metric=event.metric,
        state="active",
        enter_count=1,
        clear_count=0,
        first_seen_ts=event.onset_ts,
        last_seen_ts=event.event_ts,
        active_event_id=event.id,
        state_values={},
    )
    session.add(state)
    session.flush()
    return state


def _open_event(session: Session, key: str) -> Event | None:
    return session.scalar(
        select(Event)
        .where(Event.dedupe_key == key)
        .where(Event.status.in_(ACTIVE_STATUSES))
        .order_by(Event.created_at.desc())
        .limit(1)
    )


def _highest_priority_candidates(
    candidates: Iterable[EventCandidate],
) -> dict[str, EventCandidate]:
    selected: dict[str, EventCandidate] = {}
    for candidate in candidates:
        key = dedupe_key_for_candidate(candidate)
        if key not in selected:
            selected[key] = candidate
            continue
        if candidate_priority_score(candidate) >= candidate_priority_score(selected[key]):
            selected[key] = candidate
    return selected


def _candidate_strength(candidate: EventCandidate) -> float:
    signals = candidate.signal_values
    for key in ("z_score", "level_jump", "abs_error", "difference", "gap"):
        value = signals.get(key)
        if value is not None:
            return abs(float(value))
    return LEGACY_STRENGTH_BY_SEVERITY.get(candidate.severity, 1.0)


def _candidate_confidence(candidate: EventCandidate) -> float:
    if candidate.score_inputs:
        return float(candidate.score_inputs.get("confidence", 1.0))
    return 1.0


def _event_evidence(
    candidate: EventCandidate,
    *,
    peak_strength: float,
    clear_count: int,
) -> dict[str, Any]:
    return {
        **dict(candidate.evidence or candidate.signal_values),
        "lifecycle": {
            "peak_strength": peak_strength,
            "clear_count": clear_count,
            "last_candidate_ts": candidate.event_ts.isoformat(),
        },
    }


def _merged_ids(existing: Iterable[int], incoming: Iterable[int]) -> list[int]:
    merged: list[int] = []
    for value in [*existing, *incoming]:
        int_value = int(value)
        if int_value not in merged:
            merged.append(int_value)
    return merged
