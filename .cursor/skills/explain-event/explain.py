#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.models import Event, Reading  # noqa: E402


def explain_event(event_id: int) -> dict[str, Any]:
    engine = build_engine(get_settings().database_url)
    SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with SessionLocal() as session:
        event = session.get(Event, event_id)
        if event is None:
            return {"error": f"event_id {event_id} not found"}
        readings = _supporting_readings(session, event.supporting_reading_ids or [])
        return {
            "event": _event_to_dict(event),
            "supporting_readings": [_reading_to_dict(reading) for reading in readings],
        }


def _supporting_readings(session: Session, reading_ids: list[int]) -> list[Reading]:
    if not reading_ids:
        return []
    return list(
        session.scalars(
            select(Reading)
            .where(Reading.id.in_(reading_ids))
            .order_by(Reading.observation_ts),
        ).all(),
    )


def _event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "city": event.city,
        "event_ts": event.event_ts.isoformat(),
        "created_at": event.created_at.isoformat(),
        "event_type": event.event_type,
        "severity": event.severity,
        "metric": event.metric,
        "status": event.status,
        "priority_score": event.priority_score,
        "confidence": event.confidence,
        "onset_ts": event.onset_ts.isoformat() if event.onset_ts else None,
        "peak_ts": event.peak_ts.isoformat() if event.peak_ts else None,
        "resolved_ts": event.resolved_ts.isoformat() if event.resolved_ts else None,
        "detector_name": event.detector_name,
        "detector_version": event.detector_version,
        "dedupe_key": event.dedupe_key,
        "related_event_ids": event.related_event_ids,
        "signal_values": event.signal_values,
        "reason": event.reason,
        "evidence": event.evidence,
        "supporting_reading_ids": event.supporting_reading_ids,
    }


def _reading_to_dict(reading: Reading) -> dict[str, Any]:
    return {
        "id": reading.id,
        "city": reading.city,
        "observation_ts": reading.observation_ts.isoformat(),
        "temperature_2m": reading.temperature_2m,
        "apparent_temperature": reading.apparent_temperature,
        "precipitation": reading.precipitation,
        "wind_speed_10m": reading.wind_speed_10m,
        "weather_code": reading.weather_code,
        "surface_pressure": reading.surface_pressure,
        "pressure_msl": reading.pressure_msl,
        "relative_humidity_2m": reading.relative_humidity_2m,
        "dew_point_2m": reading.dew_point_2m,
        "wind_gusts_10m": reading.wind_gusts_10m,
        "cloud_cover": reading.cloud_cover,
        "snowfall": reading.snowfall,
        "snow_depth": reading.snow_depth,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Explain a stored WatchAgent event.")
    parser.add_argument("--event-id", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(explain_event(args.event_id), indent=2, default=str))


if __name__ == "__main__":
    main()
