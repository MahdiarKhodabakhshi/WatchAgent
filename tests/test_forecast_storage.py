"""Tests for forecast storage functions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Forecast, Reading
from app.storage import forecast_comparison_pairs, matching_forecast, store_forecast_if_new

BASE_TS = datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc)


def _fc_data(
    *,
    city: str = "Toronto",
    target_ts: datetime | None = None,
    issued_at: datetime | None = None,
    lead_hours: int = 6,
    temperature_2m: float = 20.0,
    weather_code: int = 0,
) -> dict:
    return {
        "city": city,
        "target_ts": target_ts or BASE_TS,
        "issued_at": issued_at or (BASE_TS - timedelta(hours=lead_hours)),
        "lead_hours": lead_hours,
        "temperature_2m": temperature_2m,
        "precipitation": 0.0,
        "wind_speed_10m": 10.0,
        "weather_code": weather_code,
    }


def test_store_forecast_keeps_earliest_lead(db_session: Session) -> None:
    """Inserting the same (city, target_ts) twice keeps the first (earliest lead)."""
    first = store_forecast_if_new(
        db_session,
        _fc_data(lead_hours=8, temperature_2m=15.0),
    )
    db_session.flush()
    assert first is not None
    assert first.lead_hours == 8

    second = store_forecast_if_new(
        db_session,
        _fc_data(lead_hours=3, temperature_2m=16.0),
    )
    db_session.flush()
    assert second is None

    stored = db_session.get(Forecast, first.id)
    assert stored is not None
    assert stored.temperature_2m == 15.0
    assert stored.lead_hours == 8


def test_store_forecast_allows_different_targets(db_session: Session) -> None:
    """Different target_ts values for the same city are stored independently."""
    ts1 = BASE_TS
    ts2 = BASE_TS + timedelta(hours=1)
    f1 = store_forecast_if_new(db_session, _fc_data(target_ts=ts1))
    f2 = store_forecast_if_new(db_session, _fc_data(target_ts=ts2))
    db_session.flush()

    assert f1 is not None
    assert f2 is not None
    assert f1.id != f2.id


def test_matching_forecast_within_lead_window(db_session: Session) -> None:
    """matching_forecast returns only forecasts within the lead window."""
    store_forecast_if_new(db_session, _fc_data(lead_hours=6))
    db_session.flush()

    found = matching_forecast(db_session, "Toronto", BASE_TS, min_lead=3, max_lead=12)
    assert found is not None
    assert found.lead_hours == 6

    not_found = matching_forecast(
        db_session, "Toronto", BASE_TS, min_lead=7, max_lead=12,
    )
    assert not_found is None


def test_matching_forecast_returns_none_for_missing(db_session: Session) -> None:
    """No stored forecast for a target_ts returns None."""
    result = matching_forecast(db_session, "Toronto", BASE_TS, min_lead=3, max_lead=12)
    assert result is None


def test_forecast_comparison_pairs_are_global_not_city_filtered(db_session: Session) -> None:
    for idx, city in enumerate(("Toronto", "Ottawa")):
        target_ts = BASE_TS - timedelta(hours=idx + 1)
        db_session.add(
            Reading(
                city=city,
                observation_ts=target_ts,
                polled_at=target_ts + timedelta(minutes=5),
                temperature_2m=20.0 + idx,
                apparent_temperature=20.0 + idx,
                precipitation=0.0,
                wind_speed_10m=10.0,
                weather_code=0,
            )
        )
        db_session.add(
            Forecast(
                city=city,
                target_ts=target_ts,
                issued_at=target_ts - timedelta(hours=6),
                lead_hours=6,
                temperature_2m=19.0 + idx,
                precipitation=0.0,
                wind_speed_10m=10.0,
                weather_code=0,
            )
        )
    db_session.commit()

    pairs = forecast_comparison_pairs(db_session, BASE_TS)

    assert {reading.city for reading, _forecast in pairs} == {"Toronto", "Ottawa"}
