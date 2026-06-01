from collections.abc import Callable

from app.detection import detect
from app.models import Reading


def test_statistical_detectors_do_not_fire_with_short_history(
    reading_factory: Callable[..., Reading],
) -> None:
    history = [
        reading_factory(id=i + 1, hours_offset=-(i + 1), temperature_2m=20.0)
        for i in range(11)
    ]
    current = reading_factory(id=100, temperature_2m=999.0, weather_code=0)

    events = detect(current, history)

    statistical_types = {
        "temperature_shock",
        "pressure_plunge",
        "warm_spell",
        "cold_spell",
        "heavy_rain_burst",
        "wind_gust_burst",
        "heat_stress",
        "cold_stress",
        "forecast_bust",
        "spatial_anomaly",
    }
    assert not any(event.event_type in statistical_types for event in events)


def test_empty_history_returns_no_events(
    reading_factory: Callable[..., Reading],
) -> None:
    current = reading_factory(id=100, temperature_2m=999.0, weather_code=0)

    assert detect(current, []) == []
