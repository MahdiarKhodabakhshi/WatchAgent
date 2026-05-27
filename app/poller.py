from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timezone
from uuid import uuid4

import httpx
import structlog
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.detection import detect
from app.open_meteo import CITIES, City, fetch_city_reading
from app.storage import (
    latest_peer_readings,
    recent_history,
    store_events,
    store_reading_if_new,
)

log = structlog.get_logger()
SessionFactory = Callable[[], Session]


async def poll_once(
    client: httpx.AsyncClient,
    *,
    session_factory: SessionFactory,
    settings: Settings | None = None,
    trace_id: str | None = None,
) -> None:
    resolved_settings = settings or get_settings()
    resolved_trace_id = trace_id or str(uuid4())

    results = await asyncio.gather(
        *(
            fetch_city_with_retries(client, city, resolved_settings, resolved_trace_id)
            for city in CITIES
        ),
        return_exceptions=True,
    )

    for city, result in zip(CITIES, results, strict=True):
        if isinstance(result, Exception):
            log.warning(
                "poll.city.skipped",
                city=city.name,
                error=str(result),
                trace_id=resolved_trace_id,
            )
            continue
        process_reading(result, session_factory, resolved_trace_id)


async def fetch_city_with_retries(
    client: httpx.AsyncClient,
    city: City,
    settings: Settings,
    trace_id: str,
) -> dict:
    for attempt in range(1, settings.max_retries + 1):
        try:
            return await fetch_city_reading(client, city, settings)
        except (httpx.HTTPError, ValueError) as exc:
            if attempt >= settings.max_retries:
                log.error(
                    "poll.city.failed",
                    city=city.name,
                    attempt=attempt,
                    error=str(exc),
                    trace_id=trace_id,
                )
                raise
            backoff = 2 ** attempt
            http_status = getattr(getattr(exc, "response", None), "status_code", None)
            log.warning(
                "poll.retry",
                city=city.name,
                http_status=http_status,
                attempt=attempt,
                next_retry_seconds=backoff,
                trace_id=trace_id,
            )
            await asyncio.sleep(backoff)
    raise RuntimeError("unreachable retry loop state")


def process_reading(
    reading_data: dict,
    session_factory: SessionFactory,
    trace_id: str,
) -> None:
    with session_factory() as session:
        reading = store_reading_if_new(session, reading_data)
        if reading is None:
            session.commit()
            log.debug(
                "reading.duplicate",
                city=reading_data["city"],
                observation_ts=reading_data["observation_ts"].isoformat(),
                trace_id=trace_id,
            )
            return

        history = recent_history(session, reading.city, before=reading.observation_ts, hours=48)
        peers = latest_peer_readings(
            session,
            exclude_city=reading.city,
            at_or_before=reading.observation_ts,
        )
        events = detect(reading, history, peers)
        stored_events = store_events(session, events)
        session.commit()

        log.info(
            "reading.processed",
            city=reading.city,
            observation_ts=reading.observation_ts.astimezone(timezone.utc).isoformat(),
            reading_id=reading.id,
            event_count=len(stored_events),
            trace_id=trace_id,
        )


async def run_poller(
    *,
    session_factory: SessionFactory,
    settings: Settings | None = None,
) -> None:
    resolved_settings = settings or get_settings()
    timeout = httpx.Timeout(resolved_settings.open_meteo_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while True:
            trace_id = str(uuid4())
            log.info("poll.cycle.start", trace_id=trace_id)
            try:
                await poll_once(
                    client,
                    session_factory=session_factory,
                    settings=resolved_settings,
                    trace_id=trace_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("poll.cycle.unexpected_error", error=str(exc), trace_id=trace_id)
            await asyncio.sleep(resolved_settings.poll_interval_seconds)
