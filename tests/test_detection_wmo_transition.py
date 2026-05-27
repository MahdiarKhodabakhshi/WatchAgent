from collections.abc import Callable

from app.detection import detect
from app.models import Reading


def test_wmo_transition_fires_on_two_level_jump(
    reading_factory: Callable[..., Reading],
) -> None:
    previous = reading_factory(id=1, hours_offset=-1, weather_code=0)
    current = reading_factory(id=2, weather_code=95)

    events = detect(current, [previous])

    wmo_events = [event for event in events if event.event_type == "wmo_transition"]
    assert len(wmo_events) == 1
    assert wmo_events[0].severity == "severe"
    assert wmo_events[0].signal_values["level_jump"] == 3


def test_wmo_transition_does_not_fire_without_previous_category(
    reading_factory: Callable[..., Reading],
) -> None:
    current = reading_factory(id=2, weather_code=95)

    events = detect(current, [])

    assert not any(event.event_type == "wmo_transition" for event in events)
