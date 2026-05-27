from collections.abc import Callable

from app.detection import detect
from app.models import Reading


def test_cross_city_contrast_fires_when_gap_exceeds_recent_p95(
    reading_factory: Callable[..., Reading],
) -> None:
    history = [
        reading_factory(
            id=i + 1,
            city="Ottawa",
            hours_offset=-(i + 1),
            temperature_2m=10.0 + (i % 2),
        )
        for i in range(20)
    ]
    peer = reading_factory(id=50, city="Toronto", temperature_2m=12.0)
    current = reading_factory(id=100, city="Ottawa", temperature_2m=30.0)

    events = detect(current, history, {"Toronto": peer})

    cross_city = [
        event
        for event in events
        if event.event_type == "cross_city_contrast" and event.metric == "temperature_2m"
    ]
    assert len(cross_city) == 1
    assert cross_city[0].signal_values["peer_city"] == "Toronto"


def test_cross_city_contrast_ignores_missing_peer_data(
    reading_factory: Callable[..., Reading],
) -> None:
    history = [
        reading_factory(id=i + 1, city="Ottawa", hours_offset=-(i + 1), temperature_2m=10.0)
        for i in range(20)
    ]
    current = reading_factory(id=100, city="Ottawa", temperature_2m=30.0)

    events = detect(current, history, {})

    assert not any(event.event_type == "cross_city_contrast" for event in events)
