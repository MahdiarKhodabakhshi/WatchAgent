from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.detection.base import (
    MIN_HISTORY_FOR_STATS,
    DetectorContext,
    EventCandidate,
)
from app.detection.scoring import priority_score, severity_from_score
from app.features import Climatology, load_default_climatology

NATIVE_DETECTOR_VERSION = "native-v1"
MIN_NATIVE_HISTORY = MIN_HISTORY_FOR_STATS


def climatology_for(ctx: DetectorContext) -> Climatology:
    return ctx.climatology if ctx.climatology is not None else load_default_climatology()


def has_native_history(ctx: DetectorContext) -> bool:
    return len(ctx.history) >= MIN_NATIVE_HISTORY


def numeric_attr(item: Any, metric: str) -> float | None:
    value = getattr(item, metric, None)
    return None if value is None else float(value)


def reading_id(item: Any) -> int | None:
    value = getattr(item, "id", None)
    return None if value is None else int(value)


def supporting_ids(*items: Any) -> list[int]:
    ids: list[int] = []
    for item in items:
        item_id = reading_id(item)
        if item_id is not None and item_id not in ids:
            ids.append(item_id)
    return ids


def dedupe_key(ctx: DetectorContext, event_type: str, metric: str | None) -> str:
    return "|".join([ctx.reading.city, event_type, metric or "none"])


def make_candidate(
    ctx: DetectorContext,
    *,
    event_type: str,
    metric: str | None,
    signal_values: dict[str, Any],
    reason: str,
    score_inputs: dict[str, float],
    detector_name: str,
    supporting_readings: Iterable[Any] = (),
    evidence: dict[str, Any] | None = None,
) -> EventCandidate:
    score = priority_score(score_inputs)
    return EventCandidate(
        city=ctx.reading.city,
        event_ts=ctx.reading.observation_ts,
        event_type=event_type,
        severity=severity_from_score(score),
        metric=metric,
        signal_values=signal_values,
        reason=reason,
        supporting_reading_ids=supporting_ids(ctx.reading, *supporting_readings),
        dedupe_key=dedupe_key(ctx, event_type, metric),
        score_inputs=score_inputs,
        severity_hint=severity_from_score(score),
        evidence=evidence or signal_values,
        detector_name=detector_name,
        detector_version=NATIVE_DETECTOR_VERSION,
    )


def confidence_input(*values: float) -> float:
    usable = [max(0.0, min(1.0, float(value))) for value in values]
    if not usable:
        return 0.0
    return min(usable)
