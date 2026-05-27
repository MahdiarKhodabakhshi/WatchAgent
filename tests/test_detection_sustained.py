from collections.abc import Callable

from app.detection import detect
from app.models import Reading


def test_sustained_extreme_fires_after_three_high_readings(
    reading_factory: Callable[..., Reading],
) -> None:
    baseline = [
        reading_factory(id=i + 1, hours_offset=-(i + 3), wind_speed_10m=10.0)
        for i in range(20)
    ]
    previous = [
        reading_factory(id=30, hours_offset=-2, wind_speed_10m=30.0),
        reading_factory(id=31, hours_offset=-1, wind_speed_10m=31.0),
    ]
    current = reading_factory(id=100, wind_speed_10m=32.0)

    events = detect(current, [*previous, *baseline])

    sustained = [
        event
        for event in events
        if event.event_type == "sustained_extreme" and event.metric == "wind_speed_10m"
    ]
    assert len(sustained) == 1
    assert sustained[0].signal_values["tail"] == "upper"


def test_sustained_extreme_requires_previous_two_readings_in_same_tail(
    reading_factory: Callable[..., Reading],
) -> None:
    baseline = [
        reading_factory(id=i + 1, hours_offset=-(i + 3), wind_speed_10m=10.0)
        for i in range(20)
    ]
    previous = [
        reading_factory(id=30, hours_offset=-2, wind_speed_10m=10.0),
        reading_factory(id=31, hours_offset=-1, wind_speed_10m=31.0),
    ]
    current = reading_factory(id=100, wind_speed_10m=32.0)

    events = detect(current, [*previous, *baseline])

    assert not any(
        event.event_type == "sustained_extreme" and event.metric == "wind_speed_10m"
        for event in events
    )
