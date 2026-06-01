"""Run all labeled scenarios through detect() and assert expected event types.

This test file is part of CI — it must be fast and deterministic.
"""

from __future__ import annotations

import pytest

from app.detection.base import DetectorContext
from app.detection.registry import detect_candidates
from tests.labeled_scenarios import SCENARIOS, Scenario


@pytest.mark.parametrize(
    "scenario",
    SCENARIOS,
    ids=[s.name for s in SCENARIOS],
)
def test_scenario(scenario: Scenario) -> None:
    events = detect_candidates(
        DetectorContext(
            reading=scenario.reading,
            history=scenario.history,
            peers=scenario.peers,
            forecast=scenario.forecast,
            forecast_comparison_pairs=scenario.forecast_comparison_pairs,
            climatology=scenario.climatology,
        )
    )
    actual_types = {e.event_type for e in events}
    assert actual_types == scenario.expected_types, (
        f"Scenario '{scenario.name}': expected {scenario.expected_types}, "
        f"got {actual_types}. Events: {[e.event_type + '/' + (e.metric or '') for e in events]}"
    )
