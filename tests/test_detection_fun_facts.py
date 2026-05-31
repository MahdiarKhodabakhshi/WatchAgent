from collections.abc import Callable

from app.detection import detect
from app.detection.rules import detect_fun_facts
from app.models import Reading


def _events_of_kind(events: list, kind: str) -> list:
    return [
        event
        for event in events
        if event.event_type == "fun_fact" and event.signal_values["kind"] == kind
    ]


def test_showcase_pack_differently_fires_on_onset_not_persistence(
    reading_factory: Callable[..., Reading],
) -> None:
    peer = reading_factory(
        id=10,
        city="Ottawa",
        apparent_temperature=16.0,
        temperature_2m=16.0,
    )
    previous = reading_factory(
        id=1,
        hours_offset=-1,
        apparent_temperature=20.0,
        temperature_2m=20.0,
    )
    current = reading_factory(
        id=2,
        apparent_temperature=25.0,
        temperature_2m=20.0,
    )

    onset_events = detect_fun_facts(current, [previous], {"Ottawa": peer})

    pack_events = _events_of_kind(onset_events, "pack_differently")
    assert len(pack_events) == 1
    assert pack_events[0].severity == "info"
    assert pack_events[0].metric == "apparent_temperature"
    assert pack_events[0].signal_values["gap_magnitude_c"] == 9.0

    next_reading = reading_factory(
        id=3,
        hours_offset=1,
        apparent_temperature=26.0,
        temperature_2m=20.0,
    )
    next_peer = reading_factory(
        id=11,
        city="Ottawa",
        hours_offset=1,
        apparent_temperature=16.0,
        temperature_2m=16.0,
    )

    persisted_events = detect_fun_facts(
        next_reading,
        [current, previous],
        {"Ottawa": next_peer},
    )

    assert _events_of_kind(persisted_events, "pack_differently") == []


def test_freezing_line_fires_when_crossing_with_opposite_peer(
    reading_factory: Callable[..., Reading],
) -> None:
    previous = reading_factory(
        id=1,
        hours_offset=-1,
        temperature_2m=1.0,
        apparent_temperature=1.0,
    )
    current = reading_factory(
        id=2,
        temperature_2m=-1.0,
        apparent_temperature=-1.0,
    )
    peer = reading_factory(
        id=3,
        city="Ottawa",
        temperature_2m=2.0,
        apparent_temperature=2.0,
    )

    events = detect(current, [previous], {"Ottawa": peer})

    freezing_events = _events_of_kind(events, "freezing_line")
    assert len(freezing_events) == 1
    assert freezing_events[0].severity == "info"
    assert freezing_events[0].metric == "temperature_2m"
    assert freezing_events[0].signal_values["current_temperature_2m"] == -1.0
    assert "-1.0C" in freezing_events[0].reason


def test_freezing_line_does_not_fire_without_new_crossing(
    reading_factory: Callable[..., Reading],
) -> None:
    previous = reading_factory(
        id=1,
        hours_offset=-1,
        temperature_2m=-1.0,
        apparent_temperature=-1.0,
    )
    current = reading_factory(
        id=2,
        temperature_2m=-2.0,
        apparent_temperature=-2.0,
    )
    peer = reading_factory(
        id=3,
        city="Ottawa",
        temperature_2m=2.0,
        apparent_temperature=2.0,
    )

    events = detect_fun_facts(current, [previous], {"Ottawa": peer})

    assert _events_of_kind(events, "freezing_line") == []


def test_pack_differently_fires_when_apparent_gap_crosses_margin(
    reading_factory: Callable[..., Reading],
) -> None:
    previous = reading_factory(
        id=1,
        hours_offset=-1,
        apparent_temperature=20.0,
        temperature_2m=20.0,
    )
    current = reading_factory(
        id=2,
        apparent_temperature=25.0,
        temperature_2m=20.0,
    )
    peer = reading_factory(
        id=3,
        city="Ottawa",
        apparent_temperature=16.5,
        temperature_2m=16.5,
    )

    events = detect_fun_facts(current, [previous], {"Ottawa": peer})

    pack_events = _events_of_kind(events, "pack_differently")
    assert len(pack_events) == 1
    assert pack_events[0].signal_values["margin_c"] == 8.0
    assert "8.5C" in pack_events[0].reason


def test_pack_differently_does_not_fire_when_gap_already_large(
    reading_factory: Callable[..., Reading],
) -> None:
    previous = reading_factory(
        id=1,
        hours_offset=-1,
        apparent_temperature=25.0,
        temperature_2m=20.0,
    )
    current = reading_factory(
        id=2,
        apparent_temperature=26.0,
        temperature_2m=20.0,
    )
    peer = reading_factory(
        id=3,
        city="Ottawa",
        apparent_temperature=16.0,
        temperature_2m=16.0,
    )

    events = detect_fun_facts(current, [previous], {"Ottawa": peer})

    assert _events_of_kind(events, "pack_differently") == []


def test_local_record_fires_for_new_warm_record(
    reading_factory: Callable[..., Reading],
) -> None:
    older = [
        reading_factory(
            id=i + 10,
            hours_offset=-(i + 2),
            temperature_2m=18.0 + i % 4,
        )
        for i in range(11)
    ]
    previous = reading_factory(id=1, hours_offset=-1, temperature_2m=20.0)
    current = reading_factory(id=2, temperature_2m=22.0)

    events = detect_fun_facts(current, [previous, *older])

    warm_events = _events_of_kind(events, "warm_record")
    assert len(warm_events) == 1
    assert warm_events[0].severity == "info"
    assert warm_events[0].metric == "temperature_2m"
    assert warm_events[0].signal_values["previous_record_temperature_2m"] == 21.0
    assert "22.0C" in warm_events[0].reason


def test_local_record_fires_for_new_cold_record(
    reading_factory: Callable[..., Reading],
) -> None:
    older = [
        reading_factory(
            id=i + 10,
            hours_offset=-(i + 2),
            temperature_2m=18.0 + i % 4,
        )
        for i in range(11)
    ]
    previous = reading_factory(id=1, hours_offset=-1, temperature_2m=20.0)
    current = reading_factory(id=2, temperature_2m=17.0)

    events = detect_fun_facts(current, [previous, *older])

    cold_events = _events_of_kind(events, "cold_record")
    assert len(cold_events) == 1
    assert cold_events[0].signal_values["previous_record_temperature_2m"] == 18.0
    assert "17.0C" in cold_events[0].reason


def test_local_record_does_not_repeat_while_record_condition_persists(
    reading_factory: Callable[..., Reading],
) -> None:
    older = [
        reading_factory(id=i + 10, hours_offset=-(i + 2), temperature_2m=20.0)
        for i in range(11)
    ]
    previous = reading_factory(id=1, hours_offset=-1, temperature_2m=21.0)
    current = reading_factory(id=2, temperature_2m=22.0)

    events = detect_fun_facts(current, [previous, *older])

    assert _events_of_kind(events, "warm_record") == []


def test_fun_facts_cold_start_returns_empty_without_crashing(
    reading_factory: Callable[..., Reading],
) -> None:
    current = reading_factory(id=2, temperature_2m=-5.0, apparent_temperature=-8.0)
    previous = reading_factory(
        id=1,
        hours_offset=-1,
        temperature_2m=5.0,
        apparent_temperature=5.0,
    )

    assert detect_fun_facts(current, []) == []
    assert detect_fun_facts(current, [previous], {}) == []
