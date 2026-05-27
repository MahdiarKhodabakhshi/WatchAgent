from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings, get_settings


@dataclass(frozen=True)
class City:
    name: str
    latitude: float
    longitude: float


CITIES = (
    City("Ottawa", 45.42, -75.69),
    City("Toronto", 43.70, -79.42),
    City("Vancouver", 49.25, -123.12),
)
CITY_NAMES = tuple(city.name for city in CITIES)
CITY_BY_NAME = {city.name: city for city in CITIES}

CURRENT_VARIABLES = (
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "wind_speed_10m",
    "weather_code",
)
HOURLY_VARIABLES = CURRENT_VARIABLES


def observation_time_to_utc(time_value: str, utc_offset_seconds: int) -> datetime:
    parsed = datetime.fromisoformat(time_value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(seconds=utc_offset_seconds)))
    return parsed.astimezone(timezone.utc)


def normalize_current_response(
    city_name: str,
    payload: dict[str, Any],
    polled_at: datetime | None = None,
) -> dict[str, Any]:
    current = payload.get("current")
    if not isinstance(current, dict):
        raise ValueError("Open-Meteo response missing current block")

    offset = int(payload.get("utc_offset_seconds", 0))
    current_time = current.get("time")
    if not isinstance(current_time, str):
        raise ValueError("Open-Meteo response missing current.time")

    fetched_at = polled_at or datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        raise ValueError("polled_at must be timezone-aware")

    return {
        "city": city_name,
        "observation_ts": observation_time_to_utc(current_time, offset),
        "polled_at": fetched_at.astimezone(timezone.utc),
        "temperature_2m": _optional_float(current.get("temperature_2m")),
        "apparent_temperature": _optional_float(current.get("apparent_temperature")),
        "precipitation": _optional_float(current.get("precipitation")),
        "wind_speed_10m": _optional_float(current.get("wind_speed_10m")),
        "weather_code": _optional_int(current.get("weather_code")),
    }


async def fetch_city_reading(
    client: httpx.AsyncClient,
    city: City,
    settings: Settings | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or get_settings()
    response = await client.get(
        resolved_settings.open_meteo_base_url,
        params={
            "latitude": city.latitude,
            "longitude": city.longitude,
            "current": ",".join(CURRENT_VARIABLES),
            "timezone": "auto",
        },
    )
    response.raise_for_status()
    return normalize_current_response(city.name, response.json())


def normalize_hourly_archive_response(
    city_name: str,
    payload: dict[str, Any],
    *,
    polled_at: datetime | None = None,
) -> list[dict[str, Any]]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("Open-Meteo archive response missing hourly block")

    time_values = hourly.get("time")
    if not isinstance(time_values, list) or not all(isinstance(item, str) for item in time_values):
        raise ValueError("Open-Meteo archive response missing hourly.time array")

    offset = int(payload.get("utc_offset_seconds", 0))
    fetched_at = polled_at or datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        raise ValueError("polled_at must be timezone-aware")

    def _series(name: str) -> list[Any]:
        series = hourly.get(name)
        if series is None:
            return [None] * len(time_values)
        if not isinstance(series, list):
            raise ValueError(f"Open-Meteo archive response hourly.{name} must be an array")
        if len(series) != len(time_values):
            raise ValueError(f"Open-Meteo archive response hourly.{name} length mismatch")
        return series

    temps = _series("temperature_2m")
    apparent = _series("apparent_temperature")
    precipitation = _series("precipitation")
    wind = _series("wind_speed_10m")
    codes = _series("weather_code")

    rows: list[dict[str, Any]] = []
    for idx, ts in enumerate(time_values):
        rows.append(
            {
                "city": city_name,
                "observation_ts": observation_time_to_utc(ts, offset),
                "polled_at": fetched_at.astimezone(timezone.utc),
                "temperature_2m": _optional_float(temps[idx]),
                "apparent_temperature": _optional_float(apparent[idx]),
                "precipitation": _optional_float(precipitation[idx]),
                "wind_speed_10m": _optional_float(wind[idx]),
                "weather_code": _optional_int(codes[idx]),
            }
        )
    return rows


def _to_ymd(value: date) -> str:
    return value.isoformat()


async def fetch_city_hourly_history(
    client: httpx.AsyncClient,
    city: City,
    *,
    start_date: date,
    end_date: date,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    resolved_settings = settings or get_settings()
    response = await client.get(
        resolved_settings.open_meteo_archive_base_url,
        params={
            "latitude": city.latitude,
            "longitude": city.longitude,
            "start_date": _to_ymd(start_date),
            "end_date": _to_ymd(end_date),
            "hourly": ",".join(HOURLY_VARIABLES),
            "timezone": "auto",
        },
    )
    response.raise_for_status()
    return normalize_hourly_archive_response(city.name, response.json())


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)
