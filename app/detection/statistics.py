from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import timedelta
from math import sqrt
from typing import Any


def metric_values(readings: Iterable[Any], metric: str) -> list[float]:
    values: list[float] = []
    for reading in readings:
        value = getattr(reading, metric, None)
        if value is not None:
            values.append(float(value))
    return values


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean requires at least one value")
    return sum(values) / len(values)


def population_std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = mean(values)
    return sqrt(sum((value - mu) ** 2 for value in values) / len(values))


def percentile(values: Sequence[float], percentile_value: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile value must be between 0 and 100")

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * percentile_value / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def readings_within_hours(reading: Any, history: Iterable[Any], hours: int) -> list[Any]:
    cutoff = reading.observation_ts - timedelta(hours=hours)
    return [
        item
        for item in history
        if item.observation_ts < reading.observation_ts and item.observation_ts >= cutoff
    ]


def newest_first(readings: Iterable[Any]) -> list[Any]:
    return sorted(readings, key=lambda item: item.observation_ts, reverse=True)


def reading_ids(readings: Iterable[Any]) -> list[int]:
    return [int(item.id) for item in readings if getattr(item, "id", None) is not None]


def same_local_hour_values(
    window: Iterable[Any],
    metric: str,
    city: str,
    target_hour: int,
    tolerance: int = 1,
) -> list[float]:
    """Metric values from readings whose LOCAL hour is within +/- tolerance of target_hour."""
    from app.detection.timeofday import local_hour

    out: list[float] = []
    for r in window:
        lh = local_hour(city, r.observation_ts)
        if lh is None:
            continue
        dist = min((lh - target_hour) % 24, (target_hour - lh) % 24)
        if dist <= tolerance:
            value = getattr(r, metric, None)
            if value is not None:
                out.append(float(value))
    return out
