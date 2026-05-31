from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    city: str
    observation_ts: datetime
    polled_at: datetime
    temperature_2m: float | None
    apparent_temperature: float | None
    precipitation: float | None
    wind_speed_10m: float | None
    weather_code: int | None
    surface_pressure: float | None
    pressure_msl: float | None
    relative_humidity_2m: float | None
    dew_point_2m: float | None
    wind_gusts_10m: float | None
    cloud_cover: float | None
    snowfall: float | None
    snow_depth: float | None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    city: str
    event_ts: datetime
    created_at: datetime
    event_type: str
    severity: str
    metric: str | None
    signal_values: dict[str, Any]
    reason: str
    supporting_reading_ids: list[int]
    status: str | None
    onset_ts: datetime | None
    peak_ts: datetime | None
    resolved_ts: datetime | None
    priority_score: float | None
    confidence: float | None
    rarity_percentile: float | None
    detector_name: str | None
    detector_version: str | None
    dedupe_key: str | None
    related_event_ids: list[int] | None
    evidence: dict[str, Any] | None


class ForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    city: str
    target_ts: datetime
    issued_at: datetime
    lead_hours: int
    temperature_2m: float | None
    precipitation: float | None
    wind_speed_10m: float | None
    weather_code: int | None
    surface_pressure: float | None
    pressure_msl: float | None
    relative_humidity_2m: float | None
    dew_point_2m: float | None
    wind_gusts_10m: float | None
    cloud_cover: float | None
    snowfall: float | None
    snow_depth: float | None


class HealthResponse(BaseModel):
    status: str
    readings_stored: int
    events_stored: int


class ReadingsResponse(BaseModel):
    readings: list[ReadingOut]


class EventsResponse(BaseModel):
    events: list[EventOut]


class ForecastsResponse(BaseModel):
    forecasts: list[ForecastOut]
