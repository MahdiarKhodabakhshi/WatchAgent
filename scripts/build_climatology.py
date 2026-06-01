#!/usr/bin/env python3
"""Build the committed WatchAgent climatology artifact from Open-Meteo archive data.

This is an offline maintenance script. Runtime code loads the JSON artifact and never fetches
historical data during startup or per-reading detection.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.detection.timeofday import local_hour, local_month  # noqa: E402
from app.features import (  # noqa: E402
    DEFAULT_METRIC_EPSILONS,
    DEFAULT_PRECIP_WET_THRESHOLD_MM,
    robust_stats,
    wet_precipitation_stats,
)
from app.open_meteo import CITIES, HOURLY_VARIABLES, City  # noqa: E402

DEFAULT_START_DATE = date(2021, 1, 1)
DEFAULT_END_DATE = date(2025, 12, 31)
DEFAULT_OUTPUT = PROJECT_ROOT / "app" / "data" / "climatology.json"
MIN_BUCKET_N = 30
CONTINUOUS_METRICS = tuple(metric for metric in HOURLY_VARIABLES if metric != "weather_code")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build WatchAgent climatology JSON.")
    parser.add_argument("--start-date", type=_parse_date, default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", type=_parse_date, default=DEFAULT_END_DATE)
    parser.add_argument("--chunk-days", type=int, default=120)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    artifact = build_climatology(
        start_date=args.start_date,
        end_date=args.end_date,
        chunk_days=args.chunk_days,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {args.output}")


def build_climatology(
    *,
    start_date: date,
    end_date: date,
    chunk_days: int,
) -> dict[str, Any]:
    metric_values: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    precip_amounts: dict[tuple[str, str], list[float]] = defaultdict(list)

    with httpx.Client(timeout=60.0) as client:
        for city in CITIES:
            for rows in _fetch_city_rows(
                client,
                city,
                start_date=start_date,
                end_date=end_date,
                chunk_days=chunk_days,
            ):
                for row in rows:
                    ts = row["observation_ts"]
                    month = local_month(city.name, ts)
                    hour = local_hour(city.name, ts)
                    if month is None or hour is None:
                        continue

                    month_key = str(month)
                    hour_key = str(hour)
                    for metric in CONTINUOUS_METRICS:
                        value = row.get(metric)
                        if value is None:
                            continue
                        numeric_value = float(value)
                        metric_values[(city.name, f"{month_key}|{hour_key}", metric)].append(
                            numeric_value
                        )
                        metric_values[(city.name, month_key, metric)].append(numeric_value)
                        metric_values[(city.name, "city", metric)].append(numeric_value)

                    precip = row.get("precipitation")
                    if precip is not None:
                        amount = float(precip)
                        precip_amounts[(city.name, f"{month_key}|{hour_key}")].append(amount)
                        precip_amounts[(city.name, month_key)].append(amount)
                        precip_amounts[(city.name, "city")].append(amount)

    return _artifact_from_values(start_date, end_date, metric_values, precip_amounts)


def _artifact_from_values(
    start_date: date,
    end_date: date,
    metric_values: Mapping[tuple[str, str, str], list[float]],
    precip_amounts: Mapping[tuple[str, str], list[float]],
) -> dict[str, Any]:
    buckets: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    month_fallbacks: dict[str, dict[str, dict[str, Any]]] = {}
    city_fallbacks: dict[str, dict[str, Any]] = {}

    for (city, scope, metric), values in metric_values.items():
        stats = robust_stats(values, epsilon=DEFAULT_METRIC_EPSILONS.get(metric, 1.0))
        if "|" in scope:
            month, hour = scope.split("|", maxsplit=1)
            buckets.setdefault(city, {}).setdefault(month, {}).setdefault(hour, {})[metric] = stats
        elif scope == "city":
            city_fallbacks.setdefault(city, {})[metric] = stats
        else:
            month_fallbacks.setdefault(city, {}).setdefault(scope, {})[metric] = stats

    precip_buckets: dict[str, dict[str, dict[str, Any]]] = {}
    precip_month_fallbacks: dict[str, dict[str, Any]] = {}
    precip_city_fallbacks: dict[str, Any] = {}
    for (city, scope), amounts in precip_amounts.items():
        stats = wet_precipitation_stats(
            amounts,
            wet_threshold_mm=DEFAULT_PRECIP_WET_THRESHOLD_MM,
        )
        if "|" in scope:
            month, hour = scope.split("|", maxsplit=1)
            precip_buckets.setdefault(city, {}).setdefault(month, {})[hour] = stats
        elif scope == "city":
            precip_city_fallbacks[city] = stats
        else:
            precip_month_fallbacks.setdefault(city, {})[scope] = stats

    return {
        "version": 1,
        "source": "Open-Meteo Historical Weather API (/v1/archive, ERA5)",
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "timezone": "GMT fetch, bucketed by city local time",
        "metrics": list(CONTINUOUS_METRICS),
        "metric_epsilons": DEFAULT_METRIC_EPSILONS,
        "min_bucket_n": MIN_BUCKET_N,
        "buckets": buckets,
        "fallbacks": {
            "month": month_fallbacks,
            "city": city_fallbacks,
        },
        "precipitation": {
            "wet_threshold_mm": DEFAULT_PRECIP_WET_THRESHOLD_MM,
            "buckets": precip_buckets,
            "fallbacks": {
                "month": precip_month_fallbacks,
                "city": precip_city_fallbacks,
            },
        },
    }


def _fetch_city_rows(
    client: httpx.Client,
    city: City,
    *,
    start_date: date,
    end_date: date,
    chunk_days: int,
) -> Iterable[list[dict[str, Any]]]:
    for chunk_start, chunk_end in _chunks(start_date, end_date, chunk_days):
        response = client.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": city.latitude,
                "longitude": city.longitude,
                "start_date": chunk_start.isoformat(),
                "end_date": chunk_end.isoformat(),
                "hourly": ",".join(HOURLY_VARIABLES),
                "timezone": "GMT",
            },
        )
        response.raise_for_status()
        yield _normalize_utc_archive_rows(city.name, response.json())


def _normalize_utc_archive_rows(city_name: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, Mapping):
        raise ValueError("Open-Meteo archive response missing hourly block")
    times = hourly.get("time")
    if not isinstance(times, list):
        raise ValueError("Open-Meteo archive response missing hourly.time array")

    series_by_metric = {
        metric: _series(hourly, metric, len(times))
        for metric in HOURLY_VARIABLES
    }
    rows: list[dict[str, Any]] = []
    for idx, ts_value in enumerate(times):
        if not isinstance(ts_value, str):
            continue
        row = {
            "city": city_name,
            "observation_ts": datetime.fromisoformat(ts_value).replace(tzinfo=timezone.utc),
        }
        for metric, series in series_by_metric.items():
            value = series[idx]
            row[metric] = None if value is None else float(value)
        rows.append(row)
    return rows


def _series(hourly: Mapping[str, Any], metric: str, expected_len: int) -> list[Any]:
    values = hourly.get(metric)
    if values is None:
        return [None] * expected_len
    if not isinstance(values, list) or len(values) != expected_len:
        raise ValueError(f"Open-Meteo archive response hourly.{metric} length mismatch")
    return values


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


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


if __name__ == "__main__":
    main()
