from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.config import Settings
from app.open_meteo import CITY_BY_NAME, fetch_city_reading, normalize_current_response
from tests.fixtures.open_meteo_responses import current_payload


def test_normalize_current_response_converts_local_time_to_utc() -> None:
    reading = normalize_current_response(
        "Toronto",
        current_payload(ts="2026-05-27T14:00", utc_offset_seconds=-14400),
        polled_at=datetime(2026, 5, 27, 18, 5, tzinfo=timezone.utc),
    )

    assert reading["observation_ts"] == datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc)
    assert reading["polled_at"] == datetime(2026, 5, 27, 18, 5, tzinfo=timezone.utc)
    assert reading["temperature_2m"] == 21.5
    assert reading["surface_pressure"] == 1001.2
    assert reading["pressure_msl"] == 1013.4
    assert reading["relative_humidity_2m"] == 62.0
    assert reading["dew_point_2m"] == 14.1
    assert reading["wind_gusts_10m"] == 21.4
    assert reading["cloud_cover"] == 45.0
    assert reading["snowfall"] == 0.0
    assert reading["snow_depth"] == 0.0


def test_normalize_current_response_missing_enriched_fields_to_none() -> None:
    reading = normalize_current_response(
        "Toronto",
        {
            "utc_offset_seconds": 0,
            "current": {
                "time": "2026-05-27T18:00",
                "temperature_2m": 21.5,
                "apparent_temperature": 20.9,
                "precipitation": 0.0,
                "wind_speed_10m": 12.3,
                "weather_code": 1,
            },
        },
        polled_at=datetime(2026, 5, 27, 18, 5, tzinfo=timezone.utc),
    )

    assert reading["surface_pressure"] is None
    assert reading["relative_humidity_2m"] is None
    assert reading["wind_gusts_10m"] is None


@pytest.mark.asyncio
async def test_fetch_city_reading_uses_open_meteo_current_params() -> None:
    settings = Settings(
        open_meteo_base_url="https://weather.test/v1/forecast",
        enable_poller=False,
        enable_forecast_reconciliation=False,
    )

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://weather.test/v1/forecast").mock(
            return_value=httpx.Response(200, json=current_payload())
        )
        async with httpx.AsyncClient() as client:
            reading, forecasts = await fetch_city_reading(
                client, CITY_BY_NAME["Toronto"], settings,
            )

    assert route.called
    request = route.calls.last.request
    assert "temperature_2m" in request.url.params["current"]
    assert "surface_pressure" in request.url.params["current"]
    assert "pressure_msl" in request.url.params["current"]
    assert "relative_humidity_2m" in request.url.params["current"]
    assert "dew_point_2m" in request.url.params["current"]
    assert "wind_gusts_10m" in request.url.params["current"]
    assert "cloud_cover" in request.url.params["current"]
    assert "snowfall" in request.url.params["current"]
    assert "snow_depth" in request.url.params["current"]
    assert request.url.params["timezone"] == "auto"
    assert reading["city"] == "Toronto"
    assert forecasts == []
