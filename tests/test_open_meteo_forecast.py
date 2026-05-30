"""Tests for forecast hourly block parsing in open_meteo.py."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.config import Settings
from app.open_meteo import (
    CITY_BY_NAME,
    fetch_city_reading,
    normalize_forecast_hourly_response,
)


def _forecast_payload(
    *,
    utc_offset_seconds: int = -14400,
    current_ts: str = "2026-05-27T14:00",
    hourly_times: list[str] | None = None,
    hourly_temps: list[float] | None = None,
    hourly_codes: list[int] | None = None,
) -> dict:
    """Build a combined current+hourly Open-Meteo response."""
    times = hourly_times or [
        "2026-05-27T17:00",
        "2026-05-27T18:00",
        "2026-05-27T19:00",
    ]
    temps = hourly_temps or [22.0, 23.0, 24.0]
    codes = hourly_codes or [0, 1, 3]
    return {
        "utc_offset_seconds": utc_offset_seconds,
        "current": {
            "time": current_ts,
            "temperature_2m": 21.5,
            "apparent_temperature": 20.9,
            "precipitation": 0.0,
            "wind_speed_10m": 12.3,
            "weather_code": 1,
        },
        "hourly": {
            "time": times,
            "temperature_2m": temps,
            "precipitation": [0.0] * len(times),
            "wind_speed_10m": [10.0] * len(times),
            "weather_code": codes,
        },
    }


def test_normalize_forecast_hourly_produces_utc_tz_aware_rows() -> None:
    """Forecast hourly block parses to UTC tz-aware target_ts rows."""
    issued_at = datetime(2026, 5, 27, 18, 5, tzinfo=timezone.utc)
    payload = _forecast_payload(utc_offset_seconds=-14400)

    rows = normalize_forecast_hourly_response("Toronto", payload, issued_at=issued_at)

    assert len(rows) == 3
    for row in rows:
        assert row["target_ts"].tzinfo is not None
        assert row["city"] == "Toronto"
        assert row["issued_at"] == issued_at

    assert rows[0]["target_ts"] == datetime(2026, 5, 27, 21, 0, tzinfo=timezone.utc)
    assert rows[0]["temperature_2m"] == 22.0
    assert rows[0]["lead_hours"] == 3


def test_normalize_forecast_handles_missing_hourly_block() -> None:
    """No hourly block → empty list, no crash."""
    payload = {"utc_offset_seconds": 0, "current": {"time": "2026-05-27T18:00"}}
    issued_at = datetime(2026, 5, 27, 18, 5, tzinfo=timezone.utc)

    rows = normalize_forecast_hourly_response("Toronto", payload, issued_at=issued_at)

    assert rows == []


@pytest.mark.asyncio
async def test_fetch_with_forecast_reconciliation_requests_hourly() -> None:
    """When enabled, fetch_city_reading includes hourly params and returns forecasts."""
    settings = Settings(
        open_meteo_base_url="https://weather.test/v1/forecast",
        enable_poller=False,
        enable_forecast_reconciliation=True,
        forecast_lead_hours_max=12,
    )
    payload = _forecast_payload()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://weather.test/v1/forecast").mock(
            return_value=httpx.Response(200, json=payload),
        )
        async with httpx.AsyncClient() as client:
            reading, forecasts = await fetch_city_reading(
                client, CITY_BY_NAME["Toronto"], settings,
            )

    assert route.called
    request = route.calls.last.request
    assert "hourly" in request.url.params
    assert "forecast_hours" in request.url.params
    assert reading["city"] == "Toronto"
    assert len(forecasts) == 3
    assert forecasts[0]["target_ts"].tzinfo is not None
