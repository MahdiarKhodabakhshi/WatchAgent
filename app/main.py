from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db, init_db
from app.logging_config import configure_logging
from app.models import Event, Reading
from app.poller import run_poller
from app.schemas import EventsResponse, HealthResponse, ReadingsResponse
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
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    db: Session = Depends(get_db),
) -> dict[str, list[Reading]]:
    query = select(Reading)
    if city is not None:
        query = query.where(Reading.city == city)
    query = query.order_by(Reading.observation_ts.desc()).limit(limit)
    return {"readings": list(db.scalars(query).all())}


@app.get("/events", response_model=EventsResponse)
def get_events(
    city: Annotated[CityName | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    db: Session = Depends(get_db),
) -> dict[str, list[Event]]:
    query = select(Event)
    if city is not None:
        query = query.where(Event.city == city)
    query = query.order_by(Event.event_ts.desc()).limit(limit)
    return {"events": list(db.scalars(query).all())}
