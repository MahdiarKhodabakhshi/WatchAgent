from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

CITY_TIMEZONES: dict[str, ZoneInfo] = {
    "Ottawa": ZoneInfo("America/Toronto"),
    "Toronto": ZoneInfo("America/Toronto"),
    "Vancouver": ZoneInfo("America/Vancouver"),
}


def local_hour(city: str, observation_ts: datetime) -> int | None:
    """Local hour-of-day (0-23) for a UTC, tz-aware observation. Pure."""
    tz = CITY_TIMEZONES.get(city)
    if tz is None or observation_ts.tzinfo is None:
        return None
    return observation_ts.astimezone(tz).hour


def local_month(city: str, observation_ts: datetime) -> int | None:
    """Local calendar month (1-12) for a UTC, tz-aware observation. Pure."""
    tz = CITY_TIMEZONES.get(city)
    if tz is None or observation_ts.tzinfo is None:
        return None
    return observation_ts.astimezone(tz).month


def local_day_of_year(city: str, observation_ts: datetime) -> int | None:
    """Local day-of-year (1-366) for a UTC, tz-aware observation. Pure."""
    tz = CITY_TIMEZONES.get(city)
    if tz is None or observation_ts.tzinfo is None:
        return None
    return observation_ts.astimezone(tz).timetuple().tm_yday
