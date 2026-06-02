from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# priority_score blends two orthogonal axes plus supporting evidence:
#   * rarity     -- statistical tail position (surprisal = -log tail probability)
#   * magnitude  -- absolute physical size (mm rain, degC departure, km/h gust)
# Rarity answers "how unusual" and magnitude answers "how big"; a rare-but-small
# event and a common-but-large event therefore land at different scores.
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
# Severity bands for the DS-4 surprisal score distribution. The severe floor is set
# to the replayed incident score p90, so "severe" stays the rare top tier (~10% of
# incidents = 10.2% at floor 60, versus 24% at 55) rather than a quarter of the feed.
# Floor 60 coincides with the pre-DS-4 cut: DS-4 reshapes scores within the tier via
# surprisal + absolute magnitude rather than moving the top-tier boundary.
SEVERITY_WARNING_FLOOR = 30.0
SEVERITY_SEVERE_FLOOR = 60.0
# Replay/eval toggle. When False, detectors fall back to the pre-DS-4 clipped rarity
# and z-based magnitude so the evaluation harness can measure the scoring change on a
# fixed baseline. Production always runs with surprisal scoring enabled.
SURPRISAL_SCORING = True


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
    if score < SEVERITY_WARNING_FLOOR:
        return "info"
    if score < SEVERITY_SEVERE_FLOOR:
        return "warning"
    return "severe"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
