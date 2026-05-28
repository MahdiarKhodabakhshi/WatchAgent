from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.config import Settings
from app.open_meteo import (
    CITY_BY_NAME,
    fetch_city_hourly_history,
    normalize_hourly_archive_response,
)


def archive_payload(
    *,
    utc_offset_seconds: int = 0,
    times: list[str] | None = None,
    temps: list[float] | None = None,
) -> dict:
    times = times or ["2026-05-01T00:00", "2026-05-01T01:00"]
    temps = temps or [10.0, 11.0]
    return {
        "utc_offset_seconds": utc_offset_seconds,
        "hourly": {
            "time": times,
            "temperature_2m": temps,
            "apparent_temperature": temps,
            "precipitation": [0.0 for _ in times],
            "wind_speed_10m": [5.0 for _ in times],
            "weather_code": [1 for _ in times],
        },
    }


def test_normalize_hourly_archive_response_converts_local_time_to_utc() -> None:
    rows = normalize_hourly_archive_response(
        "Toronto",
        archive_payload(
            utc_offset_seconds=-14400,
            times=["2026-05-01T01:00"],
            temps=[10.0],
        ),
        polled_at=datetime(2026, 5, 1, 6, 5, tzinfo=timezone.utc),
    )

    assert len(rows) == 1
    assert rows[0]["city"] == "Toronto"
    assert rows[0]["observation_ts"] == datetime(2026, 5, 1, 5, 0, tzinfo=timezone.utc)
    assert rows[0]["polled_at"] == datetime(2026, 5, 1, 6, 5, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_fetch_city_hourly_history_uses_archive_params() -> None:
    settings = Settings(
        open_meteo_archive_base_url="https://archive.test/v1/archive",
        enable_poller=False,
    )

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://archive.test/v1/archive").mock(
            return_value=httpx.Response(200, json=archive_payload())
        )
        async with httpx.AsyncClient() as client:
            rows = await fetch_city_hourly_history(
                client,
                CITY_BY_NAME["Toronto"],
                start_date=datetime(2026, 5, 1, tzinfo=timezone.utc).date(),
                end_date=datetime(2026, 5, 2, tzinfo=timezone.utc).date(),
                settings=settings,
            )

    assert route.called
    request = route.calls.last.request
    assert request.url.params["hourly"]
    assert request.url.params["start_date"] == "2026-05-01"
    assert request.url.params["end_date"] == "2026-05-02"
    assert request.url.params["timezone"] == "auto"
    assert rows[0]["city"] == "Toronto"

