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
            "surface_pressure": [1001.0 for _ in times],
            "pressure_msl": [1012.0 for _ in times],
            "relative_humidity_2m": [60.0 for _ in times],
            "dew_point_2m": [4.0 for _ in times],
            "wind_gusts_10m": [12.0 for _ in times],
            "cloud_cover": [40.0 for _ in times],
            "snowfall": [0.0 for _ in times],
            "snow_depth": [0.0 for _ in times],
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
    assert rows[0]["surface_pressure"] == 1001.0
    assert rows[0]["pressure_msl"] == 1012.0
    assert rows[0]["relative_humidity_2m"] == 60.0
    assert rows[0]["dew_point_2m"] == 4.0
    assert rows[0]["wind_gusts_10m"] == 12.0
    assert rows[0]["cloud_cover"] == 40.0
    assert rows[0]["snowfall"] == 0.0
    assert rows[0]["snow_depth"] == 0.0


def test_normalize_hourly_archive_response_missing_enriched_fields_to_none() -> None:
    payload = archive_payload(times=["2026-05-01T01:00"], temps=[10.0])
    for key in (
        "surface_pressure",
        "pressure_msl",
        "relative_humidity_2m",
        "dew_point_2m",
        "wind_gusts_10m",
        "cloud_cover",
        "snowfall",
        "snow_depth",
    ):
        payload["hourly"].pop(key)

    rows = normalize_hourly_archive_response(
        "Toronto",
        payload,
        polled_at=datetime(2026, 5, 1, 6, 5, tzinfo=timezone.utc),
    )

    assert rows[0]["surface_pressure"] is None
    assert rows[0]["pressure_msl"] is None
    assert rows[0]["relative_humidity_2m"] is None
    assert rows[0]["dew_point_2m"] is None
    assert rows[0]["wind_gusts_10m"] is None
    assert rows[0]["cloud_cover"] is None
    assert rows[0]["snowfall"] is None
    assert rows[0]["snow_depth"] is None


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
    assert "surface_pressure" in request.url.params["hourly"]
    assert "pressure_msl" in request.url.params["hourly"]
    assert "relative_humidity_2m" in request.url.params["hourly"]
    assert "dew_point_2m" in request.url.params["hourly"]
    assert "wind_gusts_10m" in request.url.params["hourly"]
    assert "cloud_cover" in request.url.params["hourly"]
    assert "snowfall" in request.url.params["hourly"]
    assert "snow_depth" in request.url.params["hourly"]
    assert request.url.params["start_date"] == "2026-05-01"
    assert request.url.params["end_date"] == "2026-05-02"
    assert request.url.params["timezone"] == "auto"
    assert rows[0]["city"] == "Toronto"
