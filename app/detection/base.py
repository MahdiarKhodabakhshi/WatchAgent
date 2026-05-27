from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

EVENT_TYPES = {
    "rapid_change",
    "sustained_extreme",
    "wmo_transition",
    "comfort_divergence",
    "cross_city_contrast",
}
SEVERITIES = {"info", "warning", "severe"}
MIN_HISTORY_FOR_STATS = 12


@dataclass(frozen=True)
class Event:
    city: str
    event_ts: datetime
    event_type: str
    severity: str
    metric: str | None
    signal_values: dict[str, Any]
    reason: str
    supporting_reading_ids: list[int]

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event_type: {self.event_type}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"Unknown severity: {self.severity}")
        if self.event_ts.tzinfo is None:
            raise ValueError("event_ts must be timezone-aware")


def detect(
    reading: Any,
    history: list[Any],
    peers: dict[str, Any] | None = None,
) -> list[Event]:
    from app.detection.rules import (
        detect_comfort_divergence,
        detect_cross_city_contrast,
        detect_rapid_change,
        detect_sustained_extreme,
        detect_wmo_transition,
    )

    events: list[Event] = []
    events.extend(detect_wmo_transition(reading, history))

    if len(history) >= MIN_HISTORY_FOR_STATS:
        events.extend(detect_rapid_change(reading, history))
        events.extend(detect_sustained_extreme(reading, history))
        events.extend(detect_comfort_divergence(reading, history))

    if peers and len(history) >= MIN_HISTORY_FOR_STATS:
        events.extend(detect_cross_city_contrast(reading, history, peers))

    return events
