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


class HealthResponse(BaseModel):
    status: str
    readings_stored: int
    events_stored: int


class ReadingsResponse(BaseModel):
    readings: list[ReadingOut]


class EventsResponse(BaseModel):
    events: list[EventOut]
