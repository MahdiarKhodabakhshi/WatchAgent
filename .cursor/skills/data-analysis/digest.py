#!/usr/bin/env python3
"""Grounded natural-language digest of recent WatchAgent events.

Approach (grounded generation):
1. gather_facts()  — deterministic DB queries: counts by city/type, notable
   events with exact numbers. No LLM involved.
2. render_digest() — the LLM renders ONLY the provided facts into prose.
   It receives nothing else and is prompted to never invent numbers.

The output includes both the prose and the raw facts so a reader can verify
every claim in the briefing.

Usage
-----
    export ANTHROPIC_API_KEY=sk-...
    python3 .cursor/skills/data-analysis/digest.py              # last 24h
    python3 .cursor/skills/data-analysis/digest.py --hours 48   # last 48h
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import desc, func, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, build_engine  # noqa: E402
from app.models import Event, Reading  # noqa: E402

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

DIGEST_SYSTEM_PROMPT = (
    "Write a concise weather-operations briefing using ONLY the facts provided. "
    "Do not invent numbers. Reference specific counts, cities, event types, "
    "status, and priority_score when useful. "
    "4-6 sentences."
)


def _make_session_factory() -> sessionmaker[Session]:
    engine = build_engine(get_settings().database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def gather_facts(hours: int = 24) -> dict[str, Any]:
    """Pure DB queries — no LLM. Returns a dict of verifiable facts."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    factory = _make_session_factory()

    with factory() as session:
        total_events = _count_events_since(session, cutoff)
        by_city = _events_by_city(session, cutoff)
        by_type = _events_by_type(session, cutoff)
        by_severity = _events_by_severity(session, cutoff)
        notable = _notable_events(session, cutoff, limit=5)
        reading_summary = _latest_reading_summary(session)

    return {
        "window_hours": hours,
        "cutoff_utc": cutoff.isoformat(),
        "total_events": total_events,
        "events_by_city": by_city,
        "events_by_type": by_type,
        "events_by_severity": by_severity,
        "notable_events": notable,
        "latest_readings": reading_summary,
    }


def render_digest(facts: dict[str, Any]) -> str:
    """Use the LLM to render pre-gathered facts into prose."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return (
            "ANTHROPIC_API_KEY is required to render the digest. "
            "Raw facts are still available in the 'facts' key."
        )

    try:
        import anthropic
    except ImportError:
        return 'Install dependencies with `python3 -m pip install -e ".[dev]"`.'

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=DIGEST_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": json.dumps(facts, default=str)},
        ],
    )
    return "".join(
        getattr(block, "text", "")
        for block in response.content
        if getattr(block, "type", None) == "text"
    )


def daily_digest(hours: int = 24) -> dict[str, Any]:
    """Full digest: gather facts deterministically, then render via LLM."""
    facts = gather_facts(hours)
    prose = render_digest(facts)
    return {"digest": prose, "facts": facts}


# ---------------------------------------------------------------------------
# DB query helpers (deterministic, read-only)
# ---------------------------------------------------------------------------

def _count_events_since(session: Session, cutoff: datetime) -> int:
    return int(
        session.scalar(
            select(func.count(Event.id)).where(Event.event_ts >= cutoff)
        )
        or 0
    )


def _events_by_city(
    session: Session, cutoff: datetime,
) -> dict[str, int]:
    rows = session.execute(
        select(Event.city, func.count(Event.id))
        .where(Event.event_ts >= cutoff)
        .group_by(Event.city)
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def _events_by_type(
    session: Session, cutoff: datetime,
) -> dict[str, int]:
    rows = session.execute(
        select(Event.event_type, func.count(Event.id))
        .where(Event.event_ts >= cutoff)
        .group_by(Event.event_type)
        .order_by(func.count(Event.id).desc())
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def _events_by_severity(
    session: Session, cutoff: datetime,
) -> dict[str, int]:
    rows = session.execute(
        select(Event.severity, func.count(Event.id))
        .where(Event.event_ts >= cutoff)
        .group_by(Event.severity)
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def _notable_events(
    session: Session, cutoff: datetime, limit: int = 5,
) -> list[dict[str, Any]]:
    """Return the most recent severe/warning events with key fields."""
    rows = session.scalars(
        select(Event)
        .where(Event.event_ts >= cutoff)
        .order_by(
            desc(Event.priority_score).nulls_last(),
            Event.event_ts.desc(),
        )
        .limit(limit)
    ).all()
    return [
        {
            "city": e.city,
            "event_type": e.event_type,
            "severity": e.severity,
            "status": e.status,
            "priority_score": e.priority_score,
            "event_ts": e.event_ts.isoformat(),
            "onset_ts": e.onset_ts.isoformat() if e.onset_ts else None,
            "peak_ts": e.peak_ts.isoformat() if e.peak_ts else None,
            "resolved_ts": e.resolved_ts.isoformat() if e.resolved_ts else None,
            "reason": e.reason,
            "dedupe_key": e.dedupe_key,
        }
        for e in rows
    ]


def _latest_reading_summary(session: Session) -> dict[str, dict[str, Any]]:
    """Latest reading per city with numeric fields."""
    summary: dict[str, dict[str, Any]] = {}
    for city in ("Ottawa", "Toronto", "Vancouver"):
        r = session.scalar(
            select(Reading)
            .where(Reading.city == city)
            .order_by(Reading.observation_ts.desc())
            .limit(1)
        )
        if r:
            summary[city] = {
                "observation_ts": r.observation_ts.isoformat(),
                "temperature_2m": r.temperature_2m,
                "apparent_temperature": r.apparent_temperature,
                "precipitation": r.precipitation,
                "wind_speed_10m": r.wind_speed_10m,
                "pressure_msl": r.pressure_msl,
                "relative_humidity_2m": r.relative_humidity_2m,
                "dew_point_2m": r.dew_point_2m,
                "wind_gusts_10m": r.wind_gusts_10m,
            }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a grounded NL digest of recent events.",
    )
    parser.add_argument(
        "--hours", type=int, default=24,
        help="Look-back window in hours (default: 24).",
    )
    args = parser.parse_args()
    result = daily_digest(args.hours)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
