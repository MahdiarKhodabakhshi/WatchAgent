from collections.abc import Callable

from app.detection import detect
from app.models import Reading


def test_rapid_change_fires_on_large_z_score(
    reading_factory: Callable[..., Reading],
) -> None:
    history = [
        reading_factory(id=i + 1, hours_offset=-(i + 1), temperature_2m=20.0 + (i % 3))
        for i in range(20)
    ]
    current = reading_factory(id=100, temperature_2m=26.0)

    events = detect(current, history)

    rapid_events = [event for event in events if event.event_type == "rapid_change"]
    assert len(rapid_events) == 1
    assert rapid_events[0].severity == "severe"
    assert rapid_events[0].metric == "temperature_2m"
    assert "sigma" in rapid_events[0].reason


def test_rapid_change_does_not_fire_below_threshold(
    reading_factory: Callable[..., Reading],
) -> None:
    history = [
        reading_factory(id=i + 1, hours_offset=-(i + 1), temperature_2m=20.0 + (i % 3))
        for i in range(20)
    ]
    current = reading_factory(id=100, temperature_2m=21.2)

    events = detect(current, history)

    assert not any(event.event_type == "rapid_change" for event in events)


def test_rapid_change_handles_zero_std(
    reading_factory: Callable[..., Reading],
) -> None:
    history = [
        reading_factory(id=i + 1, hours_offset=-(i + 1), temperature_2m=20.0)
        for i in range(20)
    ]
    current = reading_factory(id=100, temperature_2m=20.1)

    events = detect(current, history)

    assert not any(event.event_type == "rapid_change" for event in events)
