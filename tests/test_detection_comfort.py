from collections.abc import Callable

from app.detection import detect
from app.models import Reading


def test_comfort_divergence_fires_above_city_calibrated_gap(
    reading_factory: Callable[..., Reading],
) -> None:
    history = [
        reading_factory(
            id=i + 1,
            hours_offset=-(i + 1),
            temperature_2m=20.0,
            apparent_temperature=21.0 + (0.1 if i % 2 else 0.0),
        )
        for i in range(20)
    ]
    current = reading_factory(id=100, temperature_2m=20.0, apparent_temperature=30.0)

    events = detect(current, history)

    comfort = [event for event in events if event.event_type == "comfort_divergence"]
    assert len(comfort) == 1
    assert comfort[0].metric == "apparent_temperature"
    assert "threshold" in comfort[0].signal_values


def test_comfort_divergence_does_not_fire_for_normal_gap(
    reading_factory: Callable[..., Reading],
) -> None:
    history = [
        reading_factory(
            id=i + 1,
            hours_offset=-(i + 1),
            temperature_2m=20.0,
            apparent_temperature=21.0 + (0.1 if i % 2 else 0.0),
        )
        for i in range(20)
    ]
    current = reading_factory(id=100, temperature_2m=20.0, apparent_temperature=21.1)

    events = detect(current, history)

    assert not any(event.event_type == "comfort_divergence" for event in events)
