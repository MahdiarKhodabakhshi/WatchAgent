from __future__ import annotations

from dataclasses import dataclass

from app.detection.base import DetectorContext, EventCandidate
from app.detection.native_common import (
    climatology_for,
    confidence_input,
    has_native_history,
    make_candidate,
    z_rarity,
)
from app.features import median, peer_z_values

SPATIAL_Z_GAP = 5.0
SPATIAL_MIN_OWN_Z = 3.0
SPATIAL_METRICS = ("temperature_2m", "wind_gusts_10m", "pressure_msl")
USE_EMPIRICAL_QUANTILE_GATES = True


@dataclass(frozen=True)
class SpatialAnomalyDetector:
    name: str = "spatial_anomaly"
    family: str = "native"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        if not has_native_history(ctx) or not ctx.peers:
            return []

        climatology = climatology_for(ctx)
        events: list[EventCandidate] = []
        for metric in SPATIAL_METRICS:
            current_z = climatology.z_hod(
                ctx.reading.city,
                metric,
                getattr(ctx.reading, metric, None),
                ctx.reading.observation_ts,
            )
            if current_z.z is None:
                continue
            own_abs_z = abs(current_z.z)
            own_threshold, threshold_source = _own_z_threshold(
                climatology,
                metric,
                current_z.z,
            )
            if own_threshold is None or own_abs_z < own_threshold:
                continue

            peers = peer_z_values(ctx.peers, metric, climatology)
            usable_peer_z = [
                value.z
                for value in peers.values()
                if value.z is not None and value.confidence > 0
            ]
            if not usable_peer_z:
                continue

            peer_median = median([float(value) for value in usable_peer_z])
            z_gap = abs(current_z.z - peer_median)
            if z_gap < SPATIAL_Z_GAP:
                continue

            signal_values = {
                "z_score": round(own_abs_z, 3),
                "signed_z_score": round(current_z.z, 3),
                "threshold_z": round(own_threshold, 3),
                "threshold_source": threshold_source,
                "threshold_quantile": climatology.empirical_upper_quantile,
                "peer_median_z": round(peer_median, 3),
                "difference": round(z_gap, 3),
                "peer_count": len(usable_peer_z),
                "baseline_bucket": current_z.bucket,
            }
            events.append(
                make_candidate(
                    ctx,
                    event_type="spatial_anomaly",
                    metric=metric,
                    signal_values=signal_values,
                    reason=(
                        f"{ctx.reading.city}'s {metric} anomaly differs from peers by "
                        f"{z_gap:.1f} sigma in climatology-normalized space."
                    ),
                    score_inputs={
                        "spatial": min(z_gap / 5.0, 1.0),
                        "rarity": z_rarity(
                            climatology,
                            metric,
                            current_z.z,
                            tail="upper" if current_z.z >= 0 else "lower",
                            legacy=min(own_abs_z / 4.0, 1.0),
                        ),
                        "confidence": confidence_input(current_z.confidence),
                    },
                    detector_name=self.name,
                    supporting_readings=ctx.peers.values(),
                )
            )
        return events


def _own_z_threshold(climatology, metric: str, signed_z: float) -> tuple[float | None, str]:
    if not USE_EMPIRICAL_QUANTILE_GATES:
        return SPATIAL_MIN_OWN_Z, "fixed_z"
    if metric == "wind_gusts_10m" and signed_z < 0:
        return None, "upper_tail_only"
    tail = "upper" if signed_z >= 0 else "lower"
    threshold = climatology.empirical_z_threshold(metric, tail)
    if threshold is None:
        return SPATIAL_MIN_OWN_Z, "fixed_z_fallback"
    return abs(threshold), f"training_{tail}_quantile"
