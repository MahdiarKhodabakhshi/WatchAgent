from __future__ import annotations

from dataclasses import dataclass

from app.detection.base import DetectorContext, EventCandidate
from app.detection.native_common import (
    climatology_for,
    confidence_input,
    has_native_history,
    make_candidate,
)
from app.features import median, peer_z_values

SPATIAL_Z_GAP = 3.0
SPATIAL_METRICS = ("temperature_2m", "wind_gusts_10m", "pressure_msl", "precipitation")


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
                "z_score": round(abs(current_z.z), 3),
                "signed_z_score": round(current_z.z, 3),
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
                        "rarity": min(abs(current_z.z) / 4.0, 1.0),
                        "confidence": confidence_input(current_z.confidence),
                    },
                    detector_name=self.name,
                    supporting_readings=ctx.peers.values(),
                )
            )
        return events
