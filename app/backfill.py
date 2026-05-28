from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from app.config import Settings, get_settings
from app.db import Base, SessionLocal, build_engine
from app.open_meteo import CITIES, fetch_city_hourly_history
from app.poller import process_reading

log = structlog.get_logger()


def _day_range(days: int) -> tuple[date, date]:
    if days < 1:
        raise ValueError("days must be >= 1")
    today = datetime.now(timezone.utc).date()
    # inclusive start/end dates for Open-Meteo archive API
    return (today - timedelta(days=days), today)


def _chunks(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    if chunk_days < 1:
        raise ValueError("chunk_days must be >= 1")
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=chunk_days - 1))
        out.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return out


async def _fetch_all_history(
    *,
    settings: Settings,
    start_date: date,
    end_date: date,
    chunk_days: int,
) -> list[dict[str, Any]]:
    timeout = httpx.Timeout(settings.open_meteo_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        all_rows: list[dict[str, Any]] = []
        for chunk_start, chunk_end in _chunks(start_date, end_date, chunk_days):
            results = await asyncio.gather(
                *(
                    fetch_city_hourly_history(
                        client,
                        city,
                        start_date=chunk_start,
                        end_date=chunk_end,
                        settings=settings,
                    )
                    for city in CITIES
                )
            )
            for rows in results:
                all_rows.extend(rows)
        return all_rows


def _sort_key(row: dict[str, Any]) -> tuple[datetime, str]:
    ts = row["observation_ts"]
    if not isinstance(ts, datetime) or ts.tzinfo is None:
        raise ValueError("row observation_ts must be a timezone-aware datetime")
    return (ts, str(row.get("city", "")))


async def backfill(
    *,
    days: int,
    chunk_days: int,
    dry_run: bool,
    settings: Settings | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or get_settings()
    start_date, end_date = _day_range(days)

    # Ensure tables exist (important when running outside FastAPI).
    engine = build_engine(resolved_settings.database_url)
    Base.metadata.create_all(engine)

    rows = await _fetch_all_history(
        settings=resolved_settings,
        start_date=start_date,
        end_date=end_date,
        chunk_days=chunk_days,
    )
    rows.sort(key=_sort_key)

    if dry_run:
        return {
            "mode": "dry_run",
            "days": days,
            "chunk_days": chunk_days,
            "rows_fetched": len(rows),
            "rows_sample": [
                {
                    "city": row["city"],
                    "observation_ts": row["observation_ts"].isoformat(),
                    "temperature_2m": row["temperature_2m"],
                }
                for row in rows[:5]
            ],
        }

    trace_id = f"backfill:{start_date.isoformat()}..{end_date.isoformat()}"
    processed = 0
    for row in rows:
        process_reading(row, SessionLocal, trace_id)
        processed += 1
        if processed % 500 == 0:
            log.info("backfill.progress", processed=processed, total=len(rows), trace_id=trace_id)

    return {
        "mode": "write",
        "days": days,
        "chunk_days": chunk_days,
        "rows_fetched": len(rows),
        "rows_processed": processed,
        "trace_id": trace_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill WatchAgent DB from Open-Meteo archive.")
    parser.add_argument("--days", type=int, default=90, help="How many days back to fetch.")
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=31,
        help="Fetch archive data in chunks to reduce payload size.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse but do not write to DB.",
    )
    args = parser.parse_args()
    result = asyncio.run(
        backfill(days=args.days, chunk_days=args.chunk_days, dry_run=args.dry_run)
    )
    print(result)


if __name__ == "__main__":
    main()

