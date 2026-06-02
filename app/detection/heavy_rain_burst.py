from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.detection.base import DetectorContext, EventCandidate
from app.detection.native_common import (
    climatology_for,
    confidence_input,
    has_native_history,
    make_candidate,
    numeric_attr,
)

MIN_HEAVY_RAIN_MM = 10.0
HEAVY_RAIN_ACCUMULATION_HOURS = 6
MIN_HEAVY_RAIN_ACCUMULATION_MM = 10.0
ECCC_ONE_HOUR_RAIN_MM = 25.0
USE_EMPIRICAL_QUANTILE_GATES = True


@dataclass(frozen=True)
class HeavyRainBurstDetector:
    name: str = "heavy_rain_burst"
    family: str = "native"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        if not has_native_history(ctx):
            return []

        climatology = climatology_for(ctx)
        precip = climatology.precipitation_features(
            ctx.reading.city,
            getattr(ctx.reading, "precipitation", None),
            ctx.reading.observation_ts,
        )
        if not precip.is_wet:
            return []

        hourly_threshold, wet_quantile, threshold_source = _hourly_threshold(
            climatology,
            precip,
        )
        if hourly_threshold is None:
            return []

        accumulation = _recent_accumulation_mm(ctx)
        accumulation_threshold = max(hourly_threshold, MIN_HEAVY_RAIN_ACCUMULATION_MM)
        hourly_fire = precip.amount_mm >= hourly_threshold
        accumulation_fire = accumulation >= accumulation_threshold
        if not hourly_fire and not accumulation_fire:
            return []

        signal_values = {
            "amount_mm": round(precip.amount_mm, 3),
            "accumulation_mm": round(accumulation, 3),
            "difference": round(max(precip.amount_mm, accumulation), 3),
            "wet_hour_p95_mm": None
            if precip.wet_amount_percentiles.get(95) is None
            else round(precip.wet_amount_percentiles[95], 3),
            "wet_hour_quantile_mm": None if wet_quantile is None else round(wet_quantile, 3),
            "absolute_hazard_floor_mm": MIN_HEAVY_RAIN_MM,
            "threshold_mm": round(hourly_threshold, 3),
            "threshold_source": threshold_source,
            "threshold_quantile": climatology.empirical_upper_quantile,
            "accumulation_hours": HEAVY_RAIN_ACCUMULATION_HOURS,
            "accumulation_threshold_mm": round(accumulation_threshold, 3),
            "trigger": "hourly" if hourly_fire else "accumulation",
            "wet_amount_percentile": precip.wet_amount_percentile,
            "wet_count": precip.wet_count,
            "total_count": precip.total_count,
            "baseline_bucket": precip.bucket,
        }
        return [
            make_candidate(
                ctx,
                event_type="heavy_rain_burst",
                metric="precipitation",
                signal_values=signal_values,
                reason=(
                    f"{ctx.reading.city} has a heavy rain burst: "
                    f"{precip.amount_mm:.1f} mm this hour and "
                    f"{accumulation:.1f} mm over {HEAVY_RAIN_ACCUMULATION_HOURS}h "
                    f"exceeds the wet-hour threshold of {hourly_threshold:.1f} mm."
                ),
                score_inputs={
                    "rarity": 1.0 if hourly_fire or accumulation_fire else 0.0,
                    "magnitude": min(
                        max(precip.amount_mm, accumulation) / 20.0,
                        1.0,
                    ),
                    "persistence": min(accumulation / 15.0, 1.0),
                    "compound": 1.0 if accumulation_fire else 0.5,
                    "confidence": confidence_input(max(precip.confidence, 0.8)),
                },
                detector_name=self.name,
            )
        ]


def _recent_accumulation_mm(ctx: DetectorContext) -> float:
    current = numeric_attr(ctx.reading, "precipitation") or 0.0
    if getattr(ctx.reading, "observation_ts", None) is None:
        return current
    cutoff = ctx.reading.observation_ts
    start = cutoff - timedelta(hours=HEAVY_RAIN_ACCUMULATION_HOURS)
    total = current
    for item in ctx.history:
        ts = getattr(item, "observation_ts", None)
        if ts is None or not (start < ts < cutoff):
            continue
        total += numeric_attr(item, "precipitation") or 0.0
    return total


def _hourly_threshold(climatology, precip) -> tuple[float | None, float | None, str]:
    if USE_EMPIRICAL_QUANTILE_GATES:
        threshold = climatology.empirical_wet_amount_threshold()
        if threshold is not None:
            return (
                max(threshold, MIN_HEAVY_RAIN_MM),
                threshold,
                "training_wet_hour_upper_quantile_with_hazard_floor",
            )
    p95 = precip.wet_amount_percentiles.get(95)
    if p95 is None:
        return None, None, "missing_wet_percentile"
    source = "fixed_wet_p95_floor" if not USE_EMPIRICAL_QUANTILE_GATES else "fixed_wet_p95_fallback"
    return max(p95, MIN_HEAVY_RAIN_MM), p95, source
