#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, build_engine  # noqa: E402
from app.detection import detect  # noqa: E402
from app.models import Event, Reading  # noqa: E402
from app.storage import latest_peer_readings, recent_history  # noqa: E402


def replay(limit: int) -> dict[str, Any]:
    engine = build_engine(get_settings().database_url)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with SessionLocal() as session:
        readings = list(
            reversed(
                session.scalars(
                    select(Reading).order_by(Reading.observation_ts.desc()).limit(limit)
                ).all()
            )
        )
        would_fire = []
        for reading in readings:
            history = recent_history(session, reading.city, before=reading.observation_ts, hours=48)
            peers = latest_peer_readings(
                session,
                exclude_city=reading.city,
                at_or_before=reading.observation_ts,
            )
            for event in detect(reading, history, peers):
                would_fire.append(
                    {
                        "reading_id": reading.id,
                        "city": event.city,
                        "event_ts": event.event_ts.isoformat(),
                        "event_type": event.event_type,
                        "severity": event.severity,
                        "metric": event.metric,
                        "reason": event.reason,
                    }
                )

        actual_events = session.scalars(
            select(Event).order_by(Event.event_ts.desc()).limit(limit)
        ).all()
        return {
            "readings_replayed": len(readings),
            "would_fire_count": len(would_fire),
            "actual_event_sample_count": len(actual_events),
            "would_fire": would_fire,
            "actual_events": [
                {
                    "id": event.id,
                    "city": event.city,
                    "event_ts": event.event_ts.isoformat(),
                    "event_type": event.event_type,
                    "metric": event.metric,
                    "reason": event.reason,
                }
                for event in actual_events
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay WatchAgent detection logic.")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    print(json.dumps(replay(args.limit), indent=2))


if __name__ == "__main__":
    main()
