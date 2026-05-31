from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db import Base


class UTCDateTime(TypeDecorator[datetime]):
    """Store timezone-aware UTC datetimes as ISO 8601 text in SQLite."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect) -> str | None:  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed")
        return value.astimezone(timezone.utc).isoformat()

    def process_result_value(self, value: str | None, _dialect) -> datetime | None:  # type: ignore[no-untyped-def]
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        UniqueConstraint("city", "observation_ts", name="uq_reading_city_observation_ts"),
        Index("idx_readings_city_ts", "city", "observation_ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(32), nullable=False)
    observation_ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    polled_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    temperature_2m: Mapped[float | None] = mapped_column(Float)
    apparent_temperature: Mapped[float | None] = mapped_column(Float)
    precipitation: Mapped[float | None] = mapped_column(Float)
    wind_speed_10m: Mapped[float | None] = mapped_column(Float)
    weather_code: Mapped[int | None] = mapped_column(Integer)
    surface_pressure: Mapped[float | None] = mapped_column(Float)
    pressure_msl: Mapped[float | None] = mapped_column(Float)
    relative_humidity_2m: Mapped[float | None] = mapped_column(Float)
    dew_point_2m: Mapped[float | None] = mapped_column(Float)
    wind_gusts_10m: Mapped[float | None] = mapped_column(Float)
    cloud_cover: Mapped[float | None] = mapped_column(Float)
    snowfall: Mapped[float | None] = mapped_column(Float)
    snow_depth: Mapped[float | None] = mapped_column(Float)


class Forecast(Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint("city", "target_ts", name="uq_forecast_city_target"),
        Index("idx_forecasts_city_target_ts", "city", "target_ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(32), nullable=False)
    target_ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    lead_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    temperature_2m: Mapped[float | None] = mapped_column(Float)
    precipitation: Mapped[float | None] = mapped_column(Float)
    wind_speed_10m: Mapped[float | None] = mapped_column(Float)
    weather_code: Mapped[int | None] = mapped_column(Integer)
    surface_pressure: Mapped[float | None] = mapped_column(Float)
    pressure_msl: Mapped[float | None] = mapped_column(Float)
    relative_humidity_2m: Mapped[float | None] = mapped_column(Float)
    dew_point_2m: Mapped[float | None] = mapped_column(Float)
    wind_gusts_10m: Mapped[float | None] = mapped_column(Float)
    cloud_cover: Mapped[float | None] = mapped_column(Float)
    snowfall: Mapped[float | None] = mapped_column(Float)
    snow_depth: Mapped[float | None] = mapped_column(Float)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("idx_events_city_ts", "city", "event_ts"),
        Index("idx_events_type", "event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(32), nullable=False)
    event_ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    metric: Mapped[str | None] = mapped_column(String(64))
    signal_values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_reading_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    onset_ts: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, default=None)
    peak_ts: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, default=None)
    resolved_ts: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, default=None)
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    rarity_percentile: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    detector_name: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    detector_version: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), nullable=True, default=None)
    related_event_ids: Mapped[list[int] | None] = mapped_column(
        JSON, nullable=True, default=list,
    )
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=dict)
