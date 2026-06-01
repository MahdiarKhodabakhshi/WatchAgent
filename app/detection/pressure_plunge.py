from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.detection.base import DetectorContext, EventCandidate
from app.detection.native_common import (
    confidence_input,
    has_native_history,
    make_candidate,
    numeric_attr,
)
from app.features import k_hour_delta, percentile

PRESSURE_PLUNGE_HOURS = 3
MIN_PRESSURE_FALL_HPA = 6.0
MIN_WIND_RISE_KMH = 8.0
MIN_CONFIRMING_GUST_KMH = 60.0


@dataclass(frozen=True)
class PressurePlungeDetector:
    name: str = "pressure_plunge"
    family: str = "native"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        if not has_native_history(ctx):
            return []

        metric = _pressure_metric(ctx.reading)
        if metric is None:
            return []

        pressure_delta = k_hour_delta(
            ctx.reading,
            ctx.history,
            metric,
            PRESSURE_PLUNGE_HOURS,
        )
        if pressure_delta is None or pressure_delta.delta >= 0:
            return []

        historical = _historical_deltas(ctx.history, metric, PRESSURE_PLUNGE_HOURS)
        if len(historical) < 6:
            return []

        p05_delta = percentile(historical, 5)
        required_fall = max(MIN_PRESSURE_FALL_HPA, abs(min(0.0, p05_delta)))
        pressure_fall = abs(pressure_delta.delta)
        if pressure_fall < required_fall:
            return []

        wind_metric, wind_delta, current_wind = _confirming_wind(ctx)
        if wind_metric is None or wind_delta is None or current_wind is None:
            return []
        if wind_delta < MIN_WIND_RISE_KMH and current_wind < MIN_CONFIRMING_GUST_KMH:
            return []

        signal_values = {
            "pressure_fall_hpa": round(pressure_fall, 3),
            "difference": round(pressure_fall, 3),
            "pressure_delta_hpa": round(pressure_delta.delta, 3),
            "delta_hours": PRESSURE_PLUNGE_HOURS,
            "p05_delta_hpa": round(p05_delta, 3),
            "required_fall_hpa": round(required_fall, 3),
            "wind_metric": wind_metric,
            "wind_rise_kmh": round(wind_delta, 3),
            "current_wind_kmh": round(current_wind, 3),
        }
        return [
            make_candidate(
                ctx,
                event_type="pressure_plunge",
                metric=metric,
                signal_values=signal_values,
                reason=(
                    f"{ctx.reading.city} shows storm-onset signals: {metric} fell "
                    f"{pressure_fall:.1f} hPa in {PRESSURE_PLUNGE_HOURS}h while "
                    f"{wind_metric} rose {wind_delta:.1f} km/h."
                ),
                score_inputs={
                    "rarity": min(pressure_fall / max(required_fall, 1.0), 1.0),
                    "magnitude": min(pressure_fall / 10.0, 1.0),
                    "compound": 1.0,
                    "confidence": confidence_input(1.0),
                },
                detector_name=self.name,
                supporting_readings=[
                    item
                    for item in ctx.history
                    if getattr(item, "id", None) == pressure_delta.previous_reading_id
                ],
            )
        ]


def _pressure_metric(reading: Any) -> str | None:
    if numeric_attr(reading, "pressure_msl") is not None:
        return "pressure_msl"
    if numeric_attr(reading, "surface_pressure") is not None:
        return "surface_pressure"
    return None


def _historical_deltas(history: list[Any], metric: str, hours: int) -> list[float]:
    ordered = sorted(
        [item for item in history if getattr(item, "observation_ts", None) is not None],
        key=lambda item: item.observation_ts,
    )
    timestamps = [item.observation_ts for item in ordered]
    deltas: list[float] = []
    for idx, item in enumerate(ordered):
        current_value = numeric_attr(item, metric)
        if current_value is None:
            continue
        target_ts = item.observation_ts - timedelta(hours=hours)
        insert_at = bisect_left(timestamps, target_ts, 0, idx)
        candidates = [
            ordered[position]
            for position in (insert_at - 1, insert_at)
            if 0 <= position < idx
            and numeric_attr(ordered[position], metric) is not None
            and abs(ordered[position].observation_ts - target_ts) <= timedelta(minutes=45)
        ]
        previous = min(
            candidates,
            key=lambda candidate: abs((candidate.observation_ts - target_ts).total_seconds()),
            default=None,
        )
        if previous is not None:
            previous_value = numeric_attr(previous, metric)
            if previous_value is not None:
                deltas.append(current_value - previous_value)
    return deltas


def _confirming_wind(ctx: DetectorContext) -> tuple[str | None, float | None, float | None]:
    for metric in ("wind_gusts_10m", "wind_speed_10m"):
        delta = k_hour_delta(ctx.reading, ctx.history, metric, PRESSURE_PLUNGE_HOURS)
        current = numeric_attr(ctx.reading, metric)
        if delta is not None and current is not None:
            return metric, delta.delta, current
    return None, None, None
