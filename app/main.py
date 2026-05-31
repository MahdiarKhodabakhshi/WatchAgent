from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db, init_db
from app.logging_config import configure_logging
from app.models import Event, Forecast, Reading
from app.poller import run_poller
from app.schemas import EventsResponse, ForecastsResponse, HealthResponse, ReadingsResponse
from app.storage import count_events, count_readings

CityName = Literal["Ottawa", "Toronto", "Vancouver"]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()

    task: asyncio.Task[None] | None = None
    if settings.enable_poller:
        task = asyncio.create_task(run_poller(session_factory=SessionLocal, settings=settings))

    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(lifespan=lifespan, title="WatchAgent")


def _utc_query_datetime(value: datetime | None, param_name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail=f"{param_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        readings_stored=count_readings(db),
        events_stored=count_events(db),
    )


@app.get("/readings", response_model=ReadingsResponse)
def get_readings(
    city: Annotated[CityName | None, Query()] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    db: Session = Depends(get_db),
) -> dict[str, list[Reading]]:
    start_utc = _utc_query_datetime(start, "start")
    end_utc = _utc_query_datetime(end, "end")
    query = select(Reading)
    if city is not None:
        query = query.where(Reading.city == city)
    if start_utc is not None:
        query = query.where(Reading.observation_ts >= start_utc)
    if end_utc is not None:
        query = query.where(Reading.observation_ts <= end_utc)
    query = query.order_by(Reading.observation_ts.desc()).limit(limit)
    return {"readings": list(db.scalars(query).all())}


@app.get("/events", response_model=EventsResponse)
def get_events(
    city: Annotated[CityName | None, Query()] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    db: Session = Depends(get_db),
) -> dict[str, list[Event]]:
    start_utc = _utc_query_datetime(start, "start")
    end_utc = _utc_query_datetime(end, "end")
    query = select(Event)
    if city is not None:
        query = query.where(Event.city == city)
    if start_utc is not None:
        query = query.where(Event.event_ts >= start_utc)
    if end_utc is not None:
        query = query.where(Event.event_ts <= end_utc)
    query = query.order_by(Event.event_ts.desc()).limit(limit)
    return {"events": list(db.scalars(query).all())}


@app.get("/forecasts", response_model=ForecastsResponse)
def get_forecasts(
    city: Annotated[CityName | None, Query()] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    db: Session = Depends(get_db),
) -> dict[str, list[Forecast]]:
    start_utc = _utc_query_datetime(start, "start")
    end_utc = _utc_query_datetime(end, "end")
    query = select(Forecast)
    if city is not None:
        query = query.where(Forecast.city == city)
    if start_utc is not None:
        query = query.where(Forecast.target_ts >= start_utc)
    if end_utc is not None:
        query = query.where(Forecast.target_ts <= end_utc)
    query = query.order_by(Forecast.target_ts.desc()).limit(limit)
    return {"forecasts": list(db.scalars(query).all())}


_FRONTEND_DIST = Path(__file__).parent / "static"
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
