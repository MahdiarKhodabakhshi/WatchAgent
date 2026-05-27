from __future__ import annotations


def current_payload(
    *,
    ts: str = "2026-05-27T14:00",
    utc_offset_seconds: int = -14400,
    temp: float = 21.5,
    apparent: float = 20.9,
    precipitation: float = 0.0,
    wind: float = 12.3,
    weather_code: int = 1,
) -> dict:
    return {
        "utc_offset_seconds": utc_offset_seconds,
        "current": {
            "time": ts,
            "temperature_2m": temp,
            "apparent_temperature": apparent,
            "precipitation": precipitation,
            "wind_speed_10m": wind,
            "weather_code": weather_code,
        },
    }
