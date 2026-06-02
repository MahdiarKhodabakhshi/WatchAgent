#!/usr/bin/env python3
"""Build the committed WatchAgent climatology artifact from Open-Meteo archive data.

This is an offline maintenance script. Runtime code loads the JSON artifact and never fetches
historical data during startup or per-reading detection.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.detection.timeofday import local_day_of_year, local_hour, local_month  # noqa: E402
from app.features import (  # noqa: E402
    DEFAULT_EMPIRICAL_LOWER_QUANTILE,
    DEFAULT_EMPIRICAL_TAIL_QUANTILE,
    DEFAULT_METRIC_EPSILONS,
    DEFAULT_PRECIP_WET_THRESHOLD_MM,
    percentile,
    robust_stats,
    wet_precipitation_stats,
)
from app.open_meteo import CITIES, HOURLY_VARIABLES, City  # noqa: E402

DEFAULT_START_DATE = date(2015, 1, 1)
DEFAULT_END_DATE = date(2021, 12, 31)
DEFAULT_OUTPUT = PROJECT_ROOT / "app" / "data" / "climatology.json"
MIN_BUCKET_N = 30
CONTINUOUS_METRICS = tuple(metric for metric in HOURLY_VARIABLES if metric != "weather_code")
BASELINE_METRICS = (
    "temperature_2m",
    "precipitation",
    "wind_gusts_10m",
    "pressure_msl",
)
ARCHIVE_FETCH_ATTEMPTS = 5
SMOOTH_WINDOW_DAYS = 15
DAYS_IN_YEAR = 366
BOUNDARY_DIAGNOSTIC_METRIC = "temperature_2m"
BOUNDARY_DIAGNOSTIC_LOCAL_HOUR = 12


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
    args.output.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"Wrote {args.output}")


def build_climatology(
    *,
    start_date: date,
    end_date: date,
    chunk_days: int,
) -> dict[str, Any]:
    metric_values: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    precip_amounts: dict[tuple[str, str], list[float]] = defaultdict(list)
    smooth_metric_values: dict[tuple[str, int, int, str], list[float]] = defaultdict(list)
    smooth_precip_amounts: dict[tuple[str, int, int], list[float]] = defaultdict(list)

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
                    day = local_day_of_year(city.name, ts)
                    hour = local_hour(city.name, ts)
                    if month is None or day is None or hour is None:
                        continue

                    month_key = str(month)
                    hour_key = str(hour)
                    for metric in BASELINE_METRICS:
                        value = row.get(metric)
                        if value is None:
                            continue
                        numeric_value = float(value)
                        metric_values[(city.name, f"{month_key}|{hour_key}", metric)].append(
                            numeric_value
                        )
                        metric_values[(city.name, month_key, metric)].append(numeric_value)
                        metric_values[(city.name, "city", metric)].append(numeric_value)
                        smooth_metric_values[(city.name, day, hour, metric)].append(
                            numeric_value
                        )

                    precip = row.get("precipitation")
                    if precip is not None:
                        amount = float(precip)
                        precip_amounts[(city.name, f"{month_key}|{hour_key}")].append(amount)
                        precip_amounts[(city.name, month_key)].append(amount)
                        precip_amounts[(city.name, "city")].append(amount)
                        smooth_precip_amounts[(city.name, day, hour)].append(amount)

    return _artifact_from_values(
        start_date,
        end_date,
        metric_values,
        precip_amounts,
        smooth_metric_values,
        smooth_precip_amounts,
    )


def _artifact_from_values(
    start_date: date,
    end_date: date,
    metric_values: Mapping[tuple[str, str, str], list[float]],
    precip_amounts: Mapping[tuple[str, str], list[float]],
    smooth_metric_values: Mapping[tuple[str, int, int, str], list[float]],
    smooth_precip_amounts: Mapping[tuple[str, int, int], list[float]],
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

    smooth_buckets = _smooth_metric_buckets(smooth_metric_values)

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

    smooth_precip_buckets = _smooth_precipitation_buckets(smooth_precip_amounts)
    empirical_thresholds = _empirical_thresholds(
        smooth_metric_values,
        precip_amounts,
        smooth_buckets,
        method=(
            "Training-window empirical residual quantiles after "
            "city/day-of-year/local-hour smooth median-MAD standardization; "
            "precipitation amount uses wet hours only."
        ),
    )
    legacy_empirical_thresholds = _legacy_empirical_thresholds(
        metric_values,
        precip_amounts,
        buckets,
    )
    diagnostics = _diagnostics(buckets, smooth_buckets)

    return {
        "version": 1,
        "source": "Open-Meteo Historical Weather API (/v1/archive, ERA5)",
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "timezone": "GMT fetch, bucketed by city local time",
        "baseline": {
            "method": "day_of_year_smoothing_window",
            "smooth_window_days": SMOOTH_WINDOW_DAYS,
            "description": (
                "For each city, local day-of-year, and local hour, median and MAD "
                "are computed from the same local hour across +/-15 training days."
            ),
        },
        "source_metrics": list(CONTINUOUS_METRICS),
        "metrics": list(BASELINE_METRICS),
        "metric_epsilons": DEFAULT_METRIC_EPSILONS,
        "min_bucket_n": MIN_BUCKET_N,
        "smooth_buckets": smooth_buckets,
        "buckets": buckets,
        "fallbacks": {
            "month": month_fallbacks,
            "city": city_fallbacks,
        },
        "empirical_thresholds": empirical_thresholds,
        "legacy_empirical_thresholds": legacy_empirical_thresholds,
        "diagnostics": diagnostics,
        "precipitation": {
            "wet_threshold_mm": DEFAULT_PRECIP_WET_THRESHOLD_MM,
            "smooth_buckets": smooth_precip_buckets,
            "buckets": precip_buckets,
            "fallbacks": {
                "month": precip_month_fallbacks,
                "city": precip_city_fallbacks,
            },
        },
    }


def _smooth_metric_buckets(
    values_by_day: Mapping[tuple[str, int, int, str], list[float]],
) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    buckets: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    city_hour_metrics = {
        (city, hour, metric)
        for city, _day, hour, metric in values_by_day
    }
    for city, hour, metric in sorted(city_hour_metrics):
        for day in range(1, DAYS_IN_YEAR + 1):
            values: list[float] = []
            for window_day in _window_days(day):
                values.extend(values_by_day.get((city, window_day, hour, metric), ()))
            if not values:
                continue
            stats = robust_stats(values, epsilon=DEFAULT_METRIC_EPSILONS.get(metric, 1.0))
            buckets.setdefault(city, {}).setdefault(str(day), {}).setdefault(str(hour), {})[
                metric
            ] = stats
    return buckets


def _smooth_precipitation_buckets(
    amounts_by_day: Mapping[tuple[str, int, int], list[float]],
) -> dict[str, dict[str, dict[str, Any]]]:
    buckets: dict[str, dict[str, dict[str, Any]]] = {}
    city_hours = {(city, hour) for city, _day, hour in amounts_by_day}
    for city, hour in sorted(city_hours):
        for day in range(1, DAYS_IN_YEAR + 1):
            amounts: list[float] = []
            for window_day in _window_days(day):
                amounts.extend(amounts_by_day.get((city, window_day, hour), ()))
            if not amounts:
                continue
            stats = wet_precipitation_stats(
                amounts,
                wet_threshold_mm=DEFAULT_PRECIP_WET_THRESHOLD_MM,
            )
            buckets.setdefault(city, {}).setdefault(str(day), {})[str(hour)] = stats
    return buckets


def _window_days(day: int) -> Iterable[int]:
    for offset in range(-SMOOTH_WINDOW_DAYS, SMOOTH_WINDOW_DAYS + 1):
        yield ((day - 1 + offset) % DAYS_IN_YEAR) + 1


def _legacy_empirical_thresholds(
    metric_values: Mapping[tuple[str, str, str], list[float]],
    precip_amounts: Mapping[tuple[str, str], list[float]],
    buckets: Mapping[str, Mapping[str, Mapping[str, Mapping[str, Any]]]],
) -> dict[str, Any]:
    residuals_by_metric: dict[str, list[float]] = defaultdict(list)
    for (city, scope, metric), values in metric_values.items():
        if "|" not in scope:
            continue
        month, hour = scope.split("|", maxsplit=1)
        stats = buckets.get(city, {}).get(month, {}).get(hour, {}).get(metric)
        if not stats:
            continue
        center = float(stats["median"])
        scale = max(float(stats["scale"]), DEFAULT_METRIC_EPSILONS.get(metric, 1.0))
        residuals_by_metric[metric].extend((value - center) / scale for value in values)

    metric_thresholds: dict[str, dict[str, float | int]] = {}
    for metric, residuals in residuals_by_metric.items():
        if not residuals:
            continue
        metric_thresholds[metric] = {
            "n": len(residuals),
            "upper_z": round(percentile(residuals, DEFAULT_EMPIRICAL_TAIL_QUANTILE), 4),
            "lower_z": round(percentile(residuals, DEFAULT_EMPIRICAL_LOWER_QUANTILE), 4),
            "abs_z": round(
                percentile([abs(value) for value in residuals], DEFAULT_EMPIRICAL_TAIL_QUANTILE),
                4,
            ),
        }

    wet_amounts: list[float] = []
    for (_city, scope), amounts in precip_amounts.items():
        if "|" not in scope:
            continue
        wet_amounts.extend(
            amount for amount in amounts if amount >= DEFAULT_PRECIP_WET_THRESHOLD_MM
        )
    if wet_amounts:
        metric_thresholds.setdefault("precipitation", {})["wet_count"] = len(wet_amounts)
        metric_thresholds["precipitation"]["wet_amount_mm"] = round(
            percentile(wet_amounts, DEFAULT_EMPIRICAL_TAIL_QUANTILE),
            4,
        )

    return _threshold_artifact(
        metric_thresholds,
        method=(
            "Training-window empirical residual quantiles after city/month/local-hour "
            "median-MAD standardization; precipitation amount uses wet hours only."
        ),
    )


def _empirical_thresholds(
    values_by_day: Mapping[tuple[str, int, int, str], list[float]],
    precip_amounts: Mapping[tuple[str, str], list[float]],
    smooth_buckets: Mapping[str, Mapping[str, Mapping[str, Mapping[str, Any]]]],
    *,
    method: str,
) -> dict[str, Any]:
    residuals_by_metric: dict[str, list[float]] = defaultdict(list)
    for (city, day, hour, metric), values in values_by_day.items():
        stats = smooth_buckets.get(city, {}).get(str(day), {}).get(str(hour), {}).get(metric)
        if not stats:
            continue
        center = float(stats["median"])
        scale = max(float(stats["scale"]), DEFAULT_METRIC_EPSILONS.get(metric, 1.0))
        residuals_by_metric[metric].extend((value - center) / scale for value in values)

    metric_thresholds: dict[str, dict[str, float | int]] = {}
    for metric, residuals in residuals_by_metric.items():
        if not residuals:
            continue
        metric_thresholds[metric] = {
            "n": len(residuals),
            "upper_z": round(percentile(residuals, DEFAULT_EMPIRICAL_TAIL_QUANTILE), 4),
            "lower_z": round(percentile(residuals, DEFAULT_EMPIRICAL_LOWER_QUANTILE), 4),
            "abs_z": round(
                percentile([abs(value) for value in residuals], DEFAULT_EMPIRICAL_TAIL_QUANTILE),
                4,
            ),
        }

    wet_amounts = _wet_amounts(precip_amounts)
    if wet_amounts:
        metric_thresholds.setdefault("precipitation", {})["wet_count"] = len(wet_amounts)
        metric_thresholds["precipitation"]["wet_amount_mm"] = round(
            percentile(wet_amounts, DEFAULT_EMPIRICAL_TAIL_QUANTILE),
            4,
        )

    return _threshold_artifact(metric_thresholds, method=method)


def _wet_amounts(precip_amounts: Mapping[tuple[str, str], list[float]]) -> list[float]:
    wet_amounts: list[float] = []
    for (_city, scope), amounts in precip_amounts.items():
        if "|" not in scope:
            continue
        wet_amounts.extend(
            amount for amount in amounts if amount >= DEFAULT_PRECIP_WET_THRESHOLD_MM
        )
    return wet_amounts


def _threshold_artifact(
    metric_thresholds: Mapping[str, Mapping[str, float | int]],
    *,
    method: str,
) -> dict[str, Any]:
    return {
        "method": method,
        "tail_probability": round((100.0 - DEFAULT_EMPIRICAL_TAIL_QUANTILE) / 100.0, 4),
        "upper_quantile": DEFAULT_EMPIRICAL_TAIL_QUANTILE,
        "lower_quantile": DEFAULT_EMPIRICAL_LOWER_QUANTILE,
        "metrics": metric_thresholds,
    }


def _diagnostics(
    legacy_buckets: Mapping[str, Mapping[str, Mapping[str, Mapping[str, Any]]]],
    smooth_buckets: Mapping[str, Mapping[str, Mapping[str, Mapping[str, Any]]]],
) -> dict[str, Any]:
    boundaries = (
        ("Dec31->Jan1", 12, 365, 1, 1),
        ("May31->Jun1", 5, 151, 6, 152),
    )
    rows: list[dict[str, Any]] = []
    hour = BOUNDARY_DIAGNOSTIC_LOCAL_HOUR
    metric = BOUNDARY_DIAGNOSTIC_METRIC
    for city in sorted(city.name for city in CITIES):
        for label, before_month, before_day, after_month, after_day in boundaries:
            legacy_before = _stats_for_boundary(
                legacy_buckets,
                city,
                str(before_month),
                str(hour),
                metric,
            )
            legacy_after = _stats_for_boundary(
                legacy_buckets,
                city,
                str(after_month),
                str(hour),
                metric,
            )
            smooth_before = _stats_for_boundary(
                smooth_buckets,
                city,
                str(before_day),
                str(hour),
                metric,
            )
            smooth_after = _stats_for_boundary(
                smooth_buckets,
                city,
                str(after_day),
                str(hour),
                metric,
            )
            if None in (legacy_before, legacy_after, smooth_before, smooth_after):
                continue
            fixed_value = (
                float(smooth_before["median"]) + float(smooth_after["median"])
            ) / 2
            legacy_before_z = _z_for_stats(fixed_value, legacy_before)
            legacy_after_z = _z_for_stats(fixed_value, legacy_after)
            smooth_before_z = _z_for_stats(fixed_value, smooth_before)
            smooth_after_z = _z_for_stats(fixed_value, smooth_after)
            rows.append(
                {
                    "city": city,
                    "boundary": label,
                    "metric": metric,
                    "local_hour": hour,
                    "fixed_value": round(fixed_value, 3),
                    "legacy_before_z": round(legacy_before_z, 3),
                    "legacy_after_z": round(legacy_after_z, 3),
                    "legacy_jump": round(abs(legacy_after_z - legacy_before_z), 3),
                    "smooth_before_z": round(smooth_before_z, 3),
                    "smooth_after_z": round(smooth_after_z, 3),
                    "smooth_jump": round(abs(smooth_after_z - smooth_before_z), 3),
                }
            )
    return {
        "boundary_continuity": {
            "method": (
                "Fixed value z-score across calendar boundaries. Legacy uses "
                "month/local-hour buckets; smooth uses day-of-year/local-hour "
                f"+/-{SMOOTH_WINDOW_DAYS} day buckets."
            ),
            "rows": rows,
        }
    }


def _stats_for_boundary(
    root: Mapping[str, Mapping[str, Mapping[str, Mapping[str, Any]]]],
    city: str,
    period: str,
    hour: str,
    metric: str,
) -> Mapping[str, Any] | None:
    stats = root.get(city, {}).get(period, {}).get(hour, {}).get(metric)
    return stats if isinstance(stats, Mapping) else None


def _z_for_stats(value: float, stats: Mapping[str, Any]) -> float:
    center = float(stats["median"])
    scale = max(
        float(stats["scale"]),
        DEFAULT_METRIC_EPSILONS.get(BOUNDARY_DIAGNOSTIC_METRIC, 1.0),
    )
    return (value - center) / scale


def _fetch_city_rows(
    client: httpx.Client,
    city: City,
    *,
    start_date: date,
    end_date: date,
    chunk_days: int,
) -> Iterable[list[dict[str, Any]]]:
    for chunk_start, chunk_end in _chunks(start_date, end_date, chunk_days):
        response = _fetch_archive_chunk(
            client,
            city,
            chunk_start=chunk_start,
            chunk_end=chunk_end,
        )
        yield _normalize_utc_archive_rows(city.name, response.json())


def _fetch_archive_chunk(
    client: httpx.Client,
    city: City,
    *,
    chunk_start: date,
    chunk_end: date,
) -> httpx.Response:
    for attempt in range(1, ARCHIVE_FETCH_ATTEMPTS + 1):
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
        if response.status_code != 429:
            response.raise_for_status()
            return response
        if attempt == ARCHIVE_FETCH_ATTEMPTS:
            response.raise_for_status()
        retry_after = response.headers.get("Retry-After")
        delay = (
            float(retry_after)
            if retry_after is not None and retry_after.isdigit()
            else min(60.0, 5.0 * 2 ** (attempt - 1))
        )
        print(
            f"Open-Meteo archive rate limited for {city.name} "
            f"{chunk_start}..{chunk_end}; retrying in {delay:.0f}s "
            f"({attempt}/{ARCHIVE_FETCH_ATTEMPTS})",
            flush=True,
        )
        time.sleep(delay)
    raise RuntimeError("unreachable archive retry state")


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
