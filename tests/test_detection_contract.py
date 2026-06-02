from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.detection import detect
from app.detection.base import DetectorContext, EventCandidate
from app.detection.explain import explain_candidate
from app.detection.registry import detect_candidates
from app.detection.scoring import candidate_priority_score, priority_score, severity_from_score
from app.models import Reading


def test_eventcandidate_is_detector_output_type(reading_factory: Callable[..., Reading]) -> None:
    reading = reading_factory(id=1)

    candidate = EventCandidate(
        city=reading.city,
        event_ts=reading.observation_ts,
        event_type="temperature_shock",
        severity="warning",
        metric="temperature_2m",
        signal_values={"z_score": 3.0},
        reason="Temperature is 3.0 sigma from baseline.",
        supporting_reading_ids=[1],
    )

    assert isinstance(candidate, EventCandidate)
    assert candidate.score_inputs == {}
    assert candidate.evidence == {}


def test_default_registry_uses_native_candidates(
    reading_factory: Callable[..., Reading],
) -> None:
    history = [
        reading_factory(id=i + 1, hours_offset=-(i + 1), temperature_2m=20.0 + (i % 3))
        for i in range(20)
    ]
    current = reading_factory(id=100, temperature_2m=26.0, weather_code=95)

    public_events = detect(current, history)

    assert all(event.detector_version == "native-v1" for event in public_events)
    assert not any(event.event_type == "wmo_transition" for event in public_events)


def test_detect_candidates_runs_registered_detectors_in_order(
    reading_factory: Callable[..., Reading],
) -> None:
    reading = reading_factory(id=1)
    ctx = DetectorContext(reading=reading, history=[])

    candidates = detect_candidates(
        ctx,
        detectors=(StaticDetector("first"), StaticDetector("second")),
    )

    assert [candidate.reason for candidate in candidates] == ["first", "second"]


def test_priority_score_uses_weighted_inputs_without_mutating_severity() -> None:
    score = priority_score(
        {
            "rarity": 1.0,
            "magnitude": 0.5,
            "persistence": 0.0,
            "compound": 0.0,
            "forecast_surprise": 0.0,
            "spatial": 0.0,
            "confidence": 1.0,
        }
    )

    assert score == 45.0
    assert severity_from_score(score) == "warning"
    assert severity_from_score(29.999) == "info"
    assert severity_from_score(30.0) == "warning"
    assert severity_from_score(59.999) == "warning"
    assert severity_from_score(60.0) == "severe"


def test_candidate_priority_score_and_explanation_are_pure_helpers(
    reading_factory: Callable[..., Reading],
) -> None:
    reading = reading_factory(id=1)
    candidate = EventCandidate(
        city=reading.city,
        event_ts=reading.observation_ts,
        event_type="forecast_bust",
        severity="warning",
        metric="temperature_2m",
        signal_values={"abs_error": 8.0, "lead_hours": 6, "forecast_temp": 20.0},
        reason="Observed temperature missed the 6h forecast by 8.0C.",
        supporting_reading_ids=[1],
        score_inputs={"forecast_surprise": 1.0, "confidence": 0.5},
    )

    explanation = explain_candidate(candidate, max_evidence_items=2)

    assert candidate_priority_score(candidate) == 12.5
    assert candidate.severity == "warning"
    assert explanation.headline == "Observed temperature missed the 6h forecast by 8.0C."
    assert explanation.evidence == {"abs_error": 8.0, "lead_hours": 6}


@dataclass(frozen=True)
class StaticDetector:
    name: str
    family: str = "test"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        return [
            EventCandidate(
                city=ctx.reading.city,
                event_ts=ctx.reading.observation_ts,
                event_type="temperature_shock",
                severity="info",
                metric="temperature_2m",
                signal_values={"name": self.name},
                reason=self.name,
                supporting_reading_ids=[],
            )
        ]
