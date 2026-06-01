from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CandidateExplanation:
    headline: str
    evidence: dict[str, Any]


def explain_candidate(candidate: Any, *, max_evidence_items: int = 3) -> CandidateExplanation:
    evidence = _candidate_evidence(candidate)
    return CandidateExplanation(
        headline=str(getattr(candidate, "reason", "")),
        evidence=dict(list(evidence.items())[:max_evidence_items]),
    )


def _candidate_evidence(candidate: Any) -> dict[str, Any]:
    explicit = getattr(candidate, "evidence", None)
    if explicit:
        return dict(explicit)
    signal_values = getattr(candidate, "signal_values", None)
    if signal_values:
        return dict(signal_values)
    return {}
