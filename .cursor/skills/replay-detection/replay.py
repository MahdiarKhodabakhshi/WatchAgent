#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.db import Base, build_engine  # noqa: E402
from app.detection import detect  # noqa: E402
from app.detection.lifecycle import LifecycleManager  # noqa: E402
from app.models import Event, Reading  # noqa: E402
from app.storage import (  # noqa: E402
    forecast_comparison_pairs,
    latest_peer_readings,
    matching_forecast,
    recent_history,
)


def replay(limit: int, *, city: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    source_engine = build_engine(settings.database_url)
    SourceSession = sessionmaker(bind=source_engine, class_=Session, expire_on_commit=False)

    temp_engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(temp_engine)
    TempSession = sessionmaker(bind=temp_engine, class_=Session, expire_on_commit=False)

    with SourceSession() as source, TempSession() as temp:
        query = select(Reading)
        if city is not None:
            query = query.where(Reading.city == city)
        query = query.order_by(Reading.observation_ts.desc()).limit(limit)
        readings = list(reversed(source.scalars(query).all()))

        manager = LifecycleManager()
        candidates_sample: list[dict[str, Any]] = []
        candidate_count = 0

        for reading in readings:
            history = recent_history(
                source,
                reading.city,
                before=reading.observation_ts,
                hours=48,
            )
            peers = latest_peer_readings(
                source,
                exclude_city=reading.city,
                at_or_before=reading.observation_ts,
            )
            forecast = matching_forecast(
                source,
                reading.city,
                reading.observation_ts,
                settings.forecast_lead_hours_min,
                settings.forecast_lead_hours_max,
            )
            comparison_pairs = forecast_comparison_pairs(source, reading.observation_ts)
            candidates = detect(
                reading,
                history,
                peers,
                forecast=forecast,
                forecast_temp_threshold=settings.forecast_temp_divergence_c,
                forecast_comparison_pairs=comparison_pairs,
            )
            candidate_count += len(candidates)
            candidates_sample.extend(
                _candidate_to_dict(reading.id, candidate)
                for candidate in candidates
            )
            manager.apply(
                temp,
                candidates,
                observed_reading=reading,
                created_at=reading.observation_ts,
            )
        temp.commit()

        incidents = temp.scalars(
            select(Event).order_by(
                desc(Event.priority_score).nulls_last(),
                Event.event_ts.desc(),
            ),
        ).all()
        actual_events = source.scalars(
            select(Event)
            .order_by(
                desc(Event.priority_score).nulls_last(),
                Event.event_ts.desc(),
            )
            .limit(limit),
        ).all()

        return {
            "readings_replayed": len(readings),
            "candidate_count": candidate_count,
            "incident_count": len(incidents),
            "candidates_sample": candidates_sample[:50],
            "replayed_incidents": [_event_to_dict(event) for event in incidents],
            "stored_event_sample": [_event_to_dict(event) for event in actual_events],
        }


def _candidate_to_dict(reading_id: int | None, candidate: Any) -> dict[str, Any]:
    return {
        "reading_id": reading_id,
        "city": candidate.city,
        "event_ts": candidate.event_ts.isoformat(),
        "event_type": candidate.event_type,
        "metric": candidate.metric,
        "dedupe_key": candidate.dedupe_key,
        "signal_values": candidate.signal_values,
        "score_inputs": candidate.score_inputs,
        "reason": candidate.reason,
    }


def _event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "city": event.city,
        "event_ts": event.event_ts.isoformat(),
        "event_type": event.event_type,
        "severity": event.severity,
        "metric": event.metric,
        "status": event.status,
        "priority_score": event.priority_score,
        "confidence": event.confidence,
        "onset_ts": event.onset_ts.isoformat() if event.onset_ts else None,
        "peak_ts": event.peak_ts.isoformat() if event.peak_ts else None,
        "resolved_ts": event.resolved_ts.isoformat() if event.resolved_ts else None,
        "dedupe_key": event.dedupe_key,
        "reason": event.reason,
        "evidence": event.evidence,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay WatchAgent native detection plus lifecycle without writing events.",
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--city", choices=["Ottawa", "Toronto", "Vancouver"])
    args = parser.parse_args()
    print(json.dumps(replay(args.limit, city=args.city), indent=2))


if __name__ == "__main__":
    main()
