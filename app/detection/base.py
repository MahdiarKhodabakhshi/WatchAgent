from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

EVENT_TYPES = {
    "rapid_change",
    "sustained_extreme",
    "wmo_transition",
    "comfort_divergence",
    "cross_city_contrast",
    "forecast_divergence",
    "fun_fact",
}
SEVERITIES = {"info", "warning", "severe"}
MIN_HISTORY_FOR_STATS = 12


@dataclass(frozen=True)
class EventCandidate:
    city: str
    event_ts: datetime
    event_type: str
    severity: str
    metric: str | None
    signal_values: dict[str, Any]
    reason: str
    supporting_reading_ids: list[int]
    dedupe_key: str | None = None
    onset_ts: datetime | None = None
    score_inputs: dict[str, float] = field(default_factory=dict)
    severity_hint: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    detector_name: str | None = None
    detector_version: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event_type: {self.event_type}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"Unknown severity: {self.severity}")
        if self.event_ts.tzinfo is None:
            raise ValueError("event_ts must be timezone-aware")
        if self.onset_ts is not None and self.onset_ts.tzinfo is None:
            raise ValueError("onset_ts must be timezone-aware")


# Backward-compatible alias while legacy rule functions are migrated incrementally.
Event = EventCandidate


@dataclass(frozen=True)
class DetectorContext:
    reading: Any
    history: list[Any]
    peers: dict[str, Any] | None = None
    forecast: Any | None = None
    forecast_temp_threshold: float | None = None
    climatology: Any | None = None
    forecast_comparison_pairs: tuple[tuple[Any, Any], ...] = ()


class Detector(Protocol):
    name: str
    family: str

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]: ...


def detect(
    reading: Any,
    history: list[Any],
    peers: dict[str, Any] | None = None,
    forecast: Any | None = None,
    forecast_temp_threshold: float | None = None,
) -> list[EventCandidate]:
    from app.detection.registry import detect_candidates

    return detect_candidates(
        DetectorContext(
            reading=reading,
            history=history,
            peers=peers,
            forecast=forecast,
            forecast_temp_threshold=forecast_temp_threshold,
        )
    )
