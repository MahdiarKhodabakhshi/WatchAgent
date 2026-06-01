from __future__ import annotations

from dataclasses import dataclass

from app.detection.base import DetectorContext, EventCandidate
from app.detection.native_common import (
    climatology_for,
    confidence_input,
    has_native_history,
    make_candidate,
)

MIN_HEAVY_RAIN_MM = 15.0
ECCC_ONE_HOUR_RAIN_MM = 25.0


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

        p95 = precip.wet_amount_percentiles.get(95)
        if p95 is None:
            return []

        threshold = max(p95, MIN_HEAVY_RAIN_MM)
        if precip.amount_mm < threshold:
            return []

        signal_values = {
            "amount_mm": round(precip.amount_mm, 3),
            "difference": round(precip.amount_mm, 3),
            "wet_hour_p95_mm": round(p95, 3),
            "threshold_mm": round(threshold, 3),
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
                    f"{precip.amount_mm:.1f} mm this hour exceeds the wet-hour "
                    f"threshold of {threshold:.1f} mm."
                ),
                score_inputs={
                    "rarity": min((precip.wet_amount_percentile or 95) / 99.0, 1.0),
                    "magnitude": min(precip.amount_mm / ECCC_ONE_HOUR_RAIN_MM, 1.0),
                    "compound": 0.5 if precip.amount_mm >= ECCC_ONE_HOUR_RAIN_MM else 0.0,
                    "confidence": confidence_input(precip.confidence),
                },
                detector_name=self.name,
            )
        ]
