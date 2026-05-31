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
    surface_pressure: float | None = 1001.2,
    pressure_msl: float | None = 1013.4,
    relative_humidity_2m: float | None = 62.0,
    dew_point_2m: float | None = 14.1,
    wind_gusts_10m: float | None = 21.4,
    cloud_cover: float | None = 45.0,
    snowfall: float | None = 0.0,
    snow_depth: float | None = 0.0,
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
            "surface_pressure": surface_pressure,
            "pressure_msl": pressure_msl,
            "relative_humidity_2m": relative_humidity_2m,
            "dew_point_2m": dew_point_2m,
            "wind_gusts_10m": wind_gusts_10m,
            "cloud_cover": cloud_cover,
            "snowfall": snowfall,
            "snow_depth": snow_depth,
        },
    }
