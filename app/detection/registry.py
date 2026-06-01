from __future__ import annotations

from dataclasses import dataclass

from app.detection.base import (
    MIN_HISTORY_FOR_STATS,
    Detector,
    DetectorContext,
    EventCandidate,
)
from app.detection.rules import (
    FORECAST_TEMP_DIVERGENCE_C,
    detect_comfort_divergence,
    detect_cross_city_contrast,
    detect_forecast_divergence,
    detect_fun_facts,
    detect_rapid_change,
    detect_sustained_extreme,
    detect_wmo_transition,
)


@dataclass(frozen=True)
class LegacyRuleAdapter:
    """Adapter that preserves the current rule-engine output order and behavior."""

    name: str = "legacy_rule_engine"
    family: str = "legacy"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        events: list[EventCandidate] = []
        events.extend(detect_wmo_transition(ctx.reading, ctx.history))

        if len(ctx.history) >= MIN_HISTORY_FOR_STATS:
            events.extend(detect_rapid_change(ctx.reading, ctx.history))
            events.extend(detect_sustained_extreme(ctx.reading, ctx.history))
            events.extend(detect_comfort_divergence(ctx.reading, ctx.history))

        if ctx.peers and len(ctx.history) >= MIN_HISTORY_FOR_STATS:
            events.extend(detect_cross_city_contrast(ctx.reading, ctx.history, ctx.peers))

        if ctx.forecast is not None:
            threshold = (
                ctx.forecast_temp_threshold
                if ctx.forecast_temp_threshold is not None
                else FORECAST_TEMP_DIVERGENCE_C
            )
            events.extend(detect_forecast_divergence(ctx.reading, ctx.forecast, threshold))

        events.extend(detect_fun_facts(ctx.reading, ctx.history, ctx.peers))
        return events


DEFAULT_DETECTORS: tuple[Detector, ...] = (LegacyRuleAdapter(),)


def detect_candidates(
    ctx: DetectorContext,
    detectors: tuple[Detector, ...] = DEFAULT_DETECTORS,
) -> list[EventCandidate]:
    candidates: list[EventCandidate] = []
    for detector in detectors:
        candidates.extend(detector.detect(ctx))
    return candidates
