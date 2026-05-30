"""Tests for the pure forecast_divergence detector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.detection.rules import detect_forecast_divergence
from app.models import Reading


@dataclass(frozen=True)
class FakeForecast:
    """Minimal forecast-like object for pure detector tests."""

    weather_code: int | None
    temperature_2m: float | None
    lead_hours: int
    precipitation: float | None = None
    wind_speed_10m: float | None = None


BASE_TS = datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc)


def _reading(
    *,
    temperature_2m: float = 20.0,
    weather_code: int = 0,
) -> Reading:
    return Reading(
        id=1,
        city="Toronto",
        observation_ts=BASE_TS,
        polled_at=BASE_TS,
        temperature_2m=temperature_2m,
        apparent_temperature=temperature_2m,
        precipitation=0.0,
        wind_speed_10m=10.0,
        weather_code=weather_code,
    )


def test_forecast_clear_actual_storm_fires() -> None:
    """WMO level jump >= 2 emits a severe event (actual worse than forecast)."""
    reading = _reading(weather_code=95)  # severe
    forecast = FakeForecast(weather_code=0, temperature_2m=20.0, lead_hours=6)  # clear

    events = detect_forecast_divergence(reading, forecast)

    wmo_events = [e for e in events if e.metric == "weather_code"]
    assert len(wmo_events) == 1
    assert wmo_events[0].event_type == "forecast_divergence"
    assert wmo_events[0].severity == "severe"
    assert wmo_events[0].signal_values["forecast_code"] == 0
    assert wmo_events[0].signal_values["actual_code"] == 95
    assert wmo_events[0].signal_values["lead_hours"] == 6
    assert "6h forecast" in wmo_events[0].reason
    assert "95" in wmo_events[0].reason


def test_forecast_storm_actual_clear_fires_warning() -> None:
    """Forecast was worse than actual → fires warning, not severe."""
    reading = _reading(weather_code=0)  # clear
    forecast = FakeForecast(weather_code=95, temperature_2m=20.0, lead_hours=8)  # severe

    events = detect_forecast_divergence(reading, forecast)

    wmo_events = [e for e in events if e.metric == "weather_code"]
    assert len(wmo_events) == 1
    assert wmo_events[0].severity == "warning"


def test_forecast_temp_miss_fires() -> None:
    """Temperature error beyond threshold emits an event with correct abs_error."""
    reading = _reading(temperature_2m=28.0, weather_code=0)
    forecast = FakeForecast(
        weather_code=0,
        temperature_2m=20.0,
        lead_hours=6,
    )

    events = detect_forecast_divergence(reading, forecast)

    temp_events = [e for e in events if e.metric == "temperature_2m"]
    assert len(temp_events) == 1
    assert temp_events[0].event_type == "forecast_divergence"
    assert temp_events[0].signal_values["abs_error"] == 8.0
    assert temp_events[0].signal_values["lead_hours"] == 6
    assert "8.0C" in temp_events[0].reason
    assert "28.0C" in temp_events[0].reason


def test_small_forecast_error_no_event() -> None:
    """Within threshold, nothing fires."""
    reading = _reading(temperature_2m=22.0, weather_code=1)
    forecast = FakeForecast(
        weather_code=0,
        temperature_2m=20.0,
        lead_hours=6,
    )

    events = detect_forecast_divergence(reading, forecast)

    assert len(events) == 0


def test_missing_forecast_fields_no_crash() -> None:
    """None temp/code handled gracefully."""
    reading = _reading(temperature_2m=20.0, weather_code=0)
    forecast = FakeForecast(
        weather_code=None,
        temperature_2m=None,
        lead_hours=6,
    )

    events = detect_forecast_divergence(reading, forecast)

    assert len(events) == 0


def test_custom_threshold_respected() -> None:
    """Passing a custom temp_threshold works."""
    reading = _reading(temperature_2m=23.0, weather_code=0)
    forecast = FakeForecast(weather_code=0, temperature_2m=20.0, lead_hours=5)

    default_events = detect_forecast_divergence(reading, forecast)
    assert len(default_events) == 0  # 3.0 < default 6.0

    custom_events = detect_forecast_divergence(
        reading, forecast, temp_threshold=2.5,
    )
    assert len(custom_events) == 1
    assert custom_events[0].signal_values["abs_error"] == 3.0
