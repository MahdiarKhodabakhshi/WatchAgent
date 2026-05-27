from __future__ import annotations

from typing import Any

from app.detection.base import MIN_HISTORY_FOR_STATS, Event
from app.detection.statistics import (
    mean,
    metric_values,
    newest_first,
    percentile,
    population_std,
    reading_ids,
    readings_within_hours,
)
from app.detection.wmo import wmo_category, wmo_level

METRICS = ("temperature_2m", "wind_speed_10m", "precipitation")
COMPARISON_METRICS = (
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "wind_speed_10m",
)
RAPID_CHANGE_Z_WARNING = 2.5
RAPID_CHANGE_Z_SEVERE = 3.5
COMFORT_STD_MULTIPLIER = 2.0
CROSS_CITY_MIN_GAP = {
    "temperature_2m": 5.0,
    "apparent_temperature": 5.0,
    "precipitation": 2.0,
    "wind_speed_10m": 15.0,
}


def detect_rapid_change(reading: Any, history: list[Any]) -> list[Event]:
    window = readings_within_hours(reading, history, 24)
    if len(window) < MIN_HISTORY_FOR_STATS:
        return []

    events: list[Event] = []
    for metric in METRICS:
        current_value = getattr(reading, metric, None)
        values = metric_values(window, metric)
        if current_value is None or len(values) < MIN_HISTORY_FOR_STATS:
            continue
        baseline_mean = mean(values)
        baseline_std = population_std(values)
        if baseline_std <= 0:
            continue

        z_score = abs((float(current_value) - baseline_mean) / baseline_std)
        if z_score < RAPID_CHANGE_Z_WARNING:
            continue

        severity = "severe" if z_score >= RAPID_CHANGE_Z_SEVERE else "warning"
        signal_values = {
            "value": round(float(current_value), 3),
            "mean": round(baseline_mean, 3),
            "std": round(baseline_std, 3),
            "z_score": round(z_score, 3),
        }
        events.append(
            Event(
                city=reading.city,
                event_ts=reading.observation_ts,
                event_type="rapid_change",
                severity=severity,
                metric=metric,
                signal_values=signal_values,
                reason=(
                    f"{_metric_label(metric)} {float(current_value):.1f} is "
                    f"{z_score:.1f} sigma from {reading.city}'s 24h mean "
                    f"of {baseline_mean:.1f}."
                ),
                supporting_reading_ids=_supporting_ids(reading, window),
            )
        )
    return events


def detect_sustained_extreme(reading: Any, history: list[Any]) -> list[Event]:
    window = readings_within_hours(reading, history, 48)
    previous_two = newest_first(window)[:2]
    if len(window) < MIN_HISTORY_FOR_STATS or len(previous_two) < 2:
        return []

    events: list[Event] = []
    for metric in METRICS:
        values = metric_values(window, metric)
        if len(values) < MIN_HISTORY_FOR_STATS:
            continue

        lower = percentile(values, 5)
        upper = percentile(values, 95)
        if lower == upper:
            continue

        streak = [reading, *previous_two]
        streak_values = [getattr(item, metric, None) for item in streak]
        if any(value is None for value in streak_values):
            continue
        numeric_streak = [float(value) for value in streak_values]

        if all(value >= upper for value in numeric_streak):
            tail = "upper"
            threshold = upper
        elif all(value <= lower for value in numeric_streak):
            tail = "lower"
            threshold = lower
        else:
            continue

        signal_values = {
            "tail": tail,
            "threshold": round(threshold, 3),
            "current_value": round(numeric_streak[0], 3),
            "previous_values": [round(value, 3) for value in numeric_streak[1:]],
            "p05": round(lower, 3),
            "p95": round(upper, 3),
        }
        events.append(
            Event(
                city=reading.city,
                event_ts=reading.observation_ts,
                event_type="sustained_extreme",
                severity="warning",
                metric=metric,
                signal_values=signal_values,
                reason=(
                    f"{_metric_label(metric)} stayed in the {tail} tail for 3 readings; "
                    f"current value {numeric_streak[0]:.1f} vs threshold {threshold:.1f}."
                ),
                supporting_reading_ids=_supporting_ids(reading, previous_two),
            )
        )
    return events


def detect_wmo_transition(reading: Any, history: list[Any]) -> list[Event]:
    previous_reading = next(
        (item for item in newest_first(history) if getattr(item, "weather_code", None) is not None),
        None,
    )
    if previous_reading is None:
        return []

    previous_level = wmo_level(previous_reading.weather_code)
    current_level = wmo_level(reading.weather_code)
    if previous_level is None or current_level is None:
        return []

    jump = current_level - previous_level
    if abs(jump) < 2:
        return []

    previous_category = wmo_category(previous_reading.weather_code)
    current_category = wmo_category(reading.weather_code)
    severity = "severe" if current_category == "severe" else "warning"
    signal_values = {
        "previous_code": previous_reading.weather_code,
        "current_code": reading.weather_code,
        "previous_level": previous_level,
        "current_level": current_level,
        "level_jump": jump,
    }
    return [
        Event(
            city=reading.city,
            event_ts=reading.observation_ts,
            event_type="wmo_transition",
            severity=severity,
            metric="weather_code",
            signal_values=signal_values,
            reason=(
                f"WMO weather moved from {previous_category} "
                f"({previous_reading.weather_code}) to {current_category} "
                f"({reading.weather_code}), a {abs(jump)} level jump."
            ),
            supporting_reading_ids=_supporting_ids(reading, [previous_reading]),
        )
    ]


def detect_comfort_divergence(reading: Any, history: list[Any]) -> list[Event]:
    current_gap = _comfort_gap(reading)
    if current_gap is None:
        return []

    window = readings_within_hours(reading, history, 48)
    gaps = [gap for item in window if (gap := _comfort_gap(item)) is not None]
    if len(gaps) < MIN_HISTORY_FOR_STATS:
        return []

    baseline_mean = mean(gaps)
    baseline_std = population_std(gaps)
    threshold = baseline_mean + COMFORT_STD_MULTIPLIER * baseline_std
    if current_gap <= threshold:
        return []

    severity = "severe" if current_gap >= threshold * 1.5 and current_gap >= 8 else "warning"
    signal_values = {
        "gap": round(current_gap, 3),
        "mean_gap": round(baseline_mean, 3),
        "std_gap": round(baseline_std, 3),
        "threshold": round(threshold, 3),
        "temperature_2m": round(float(reading.temperature_2m), 3),
        "apparent_temperature": round(float(reading.apparent_temperature), 3),
    }
    return [
        Event(
            city=reading.city,
            event_ts=reading.observation_ts,
            event_type="comfort_divergence",
            severity=severity,
            metric="apparent_temperature",
            signal_values=signal_values,
            reason=(
                f"Apparent temperature differs from actual by {current_gap:.1f}C, "
                f"above {reading.city}'s comfort threshold of {threshold:.1f}C."
            ),
            supporting_reading_ids=_supporting_ids(reading, window),
        )
    ]


def detect_cross_city_contrast(
    reading: Any,
    history: list[Any],
    peers: dict[str, Any],
) -> list[Event]:
    window = readings_within_hours(reading, history, 48)
    if len(window) < MIN_HISTORY_FOR_STATS:
        return []

    events: list[Event] = []
    for peer_city, peer in peers.items():
        for metric in COMPARISON_METRICS:
            current_value = getattr(reading, metric, None)
            peer_value = getattr(peer, metric, None)
            if current_value is None or peer_value is None:
                continue

            historical_diffs = [
                abs(float(value) - float(peer_value))
                for value in metric_values(window, metric)
            ]
            if len(historical_diffs) < MIN_HISTORY_FOR_STATS:
                continue

            threshold = percentile(historical_diffs, 95)
            current_diff = abs(float(current_value) - float(peer_value))
            if current_diff <= threshold or current_diff < CROSS_CITY_MIN_GAP[metric]:
                continue

            signal_values = {
                "peer_city": peer_city,
                "current_city_value": round(float(current_value), 3),
                "peer_city_value": round(float(peer_value), 3),
                "difference": round(current_diff, 3),
                "p95_difference": round(threshold, 3),
            }
            events.append(
                Event(
                    city=reading.city,
                    event_ts=reading.observation_ts,
                    event_type="cross_city_contrast",
                    severity="warning",
                    metric=metric,
                    signal_values=signal_values,
                    reason=(
                        f"{reading.city}-{peer_city} {_metric_label(metric)} gap is "
                        f"{current_diff:.1f}, above the recent 95th percentile "
                        f"of {threshold:.1f}."
                    ),
                    supporting_reading_ids=_supporting_ids(reading, [peer, *window]),
                )
            )
    return events


def _comfort_gap(reading: Any) -> float | None:
    actual = getattr(reading, "temperature_2m", None)
    apparent = getattr(reading, "apparent_temperature", None)
    if actual is None or apparent is None:
        return None
    return abs(float(apparent) - float(actual))


def _supporting_ids(reading: Any, history: list[Any]) -> list[int]:
    ids = reading_ids([reading])
    ids.extend(reading_ids(history))
    return ids


def _metric_label(metric: str) -> str:
    return metric.replace("_", " ")
