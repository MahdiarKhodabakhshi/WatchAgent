from __future__ import annotations

from dataclasses import dataclass

from app.detection.base import DetectorContext, EventCandidate
from app.detection.native_common import (
    climatology_for,
    confidence_input,
    has_native_history,
    make_candidate,
)

WIND_GUST_Z = 3.2
ECCC_GUST_KMH = 90.0
USE_EMPIRICAL_QUANTILE_GATES = True


@dataclass(frozen=True)
class WindGustBurstDetector:
    name: str = "wind_gust_burst"
    family: str = "native"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        if not has_native_history(ctx):
            return []

        climatology = climatology_for(ctx)
        z = climatology.z_hod(
            ctx.reading.city,
            "wind_gusts_10m",
            getattr(ctx.reading, "wind_gusts_10m", None),
            ctx.reading.observation_ts,
        )
        if z.z is None:
            return []

        z_threshold, threshold_source = _z_threshold(climatology)
        if z.z < z_threshold and z.value < ECCC_GUST_KMH:
            return []

        signal_values = {
            "value": round(z.value, 3),
            "median": None if z.median is None else round(z.median, 3),
            "z_score": round(max(z.z, 0.0), 3),
            "threshold_z": round(z_threshold, 3),
            "threshold_source": threshold_source,
            "threshold_quantile": climatology.empirical_upper_quantile,
            "gust_kmh": round(z.value, 3),
            "eccc_gust_anchor_kmh": ECCC_GUST_KMH,
            "baseline_bucket": z.bucket,
            "baseline_n": z.n,
        }
        return [
            make_candidate(
                ctx,
                event_type="wind_gust_burst",
                metric="wind_gusts_10m",
                signal_values=signal_values,
                reason=(
                    f"{ctx.reading.city} has an unusual gust burst: "
                    f"{z.value:.1f} km/h is {z.z:.1f} sigma above the local-hour baseline."
                ),
                score_inputs={
                    "rarity": min(max(z.z, 0.0) / 4.0, 1.0),
                    "magnitude": min(z.value / ECCC_GUST_KMH, 1.0),
                    "confidence": confidence_input(z.confidence),
                },
                detector_name=self.name,
            )
        ]


def _z_threshold(climatology) -> tuple[float, str]:
    if not USE_EMPIRICAL_QUANTILE_GATES:
        return WIND_GUST_Z, "fixed_z"
    threshold = climatology.empirical_z_threshold("wind_gusts_10m", "upper")
    if threshold is None:
        return WIND_GUST_Z, "fixed_z_fallback"
    return threshold, "training_upper_quantile"
