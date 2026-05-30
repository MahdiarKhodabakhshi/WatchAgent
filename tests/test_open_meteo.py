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
    assert request.url.params["timezone"] == "auto"
    assert reading["city"] == "Toronto"
    assert forecasts == []
