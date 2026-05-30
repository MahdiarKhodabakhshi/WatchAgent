"""Run all labeled scenarios through detect() and assert expected event types.

This test file is part of CI — it must be fast and deterministic.
"""

from __future__ import annotations

import pytest

from app.detection import detect
from tests.labeled_scenarios import SCENARIOS, Scenario


@pytest.mark.parametrize(
    "scenario",
    SCENARIOS,
    ids=[s.name for s in SCENARIOS],
)
def test_scenario(scenario: Scenario) -> None:
    events = detect(
        scenario.reading,
        scenario.history,
        peers=scenario.peers,
        forecast=scenario.forecast,
    )
    actual_types = {e.event_type for e in events}
    assert actual_types == scenario.expected_types, (
        f"Scenario '{scenario.name}': expected {scenario.expected_types}, "
        f"got {actual_types}. Events: {[e.event_type + '/' + (e.metric or '') for e in events]}"
    )
