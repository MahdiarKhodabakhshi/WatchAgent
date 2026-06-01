from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SCORE_WEIGHTS = {
    "rarity": 0.30,
    "magnitude": 0.20,
    "persistence": 0.15,
    "compound": 0.10,
    "forecast_surprise": 0.10,
    "spatial": 0.10,
    "confidence": 0.05,
}
LEGACY_SEVERITY_SCORE = {
    "info": 20.0,
    "warning": 45.0,
    "severe": 70.0,
}


def priority_score(
    score_inputs: Mapping[str, float],
    *,
    duplicate_penalty: float = 0.0,
) -> float:
    weighted = sum(
        weight * _clamp01(score_inputs.get(name, 0.0))
        for name, weight in SCORE_WEIGHTS.items()
    )
    return round(max(0.0, min(100.0, 100.0 * weighted - duplicate_penalty)), 3)


def candidate_priority_score(candidate: Any, *, duplicate_penalty: float = 0.0) -> float:
    explicit_inputs = getattr(candidate, "score_inputs", {})
    if explicit_inputs:
        return priority_score(explicit_inputs, duplicate_penalty=duplicate_penalty)
    return round(
        max(
            0.0,
            min(100.0, LEGACY_SEVERITY_SCORE.get(getattr(candidate, "severity", "info"), 20.0)),
        )
        - duplicate_penalty,
        3,
    )


def severity_from_score(score: float) -> str:
    if score < 30:
        return "info"
    if score < 60:
        return "warning"
    return "severe"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
