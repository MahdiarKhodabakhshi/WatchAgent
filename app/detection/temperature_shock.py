from __future__ import annotations

from dataclasses import dataclass

from app.detection.base import DetectorContext, EventCandidate
from app.detection.native_common import (
    climatology_for,
    confidence_input,
    has_native_history,
    make_candidate,
)
from app.features import k_hour_delta

TEMPERATURE_SHOCK_Z = 3.0
TEMPERATURE_SHOCK_DELTA_C = 5.0
TEMPERATURE_SHOCK_HOURS = 3


@dataclass(frozen=True)
class TemperatureShockDetector:
    name: str = "temperature_shock"
    family: str = "native"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        if not has_native_history(ctx):
            return []

        climatology = climatology_for(ctx)
        z = climatology.z_hod(
            ctx.reading.city,
            "temperature_2m",
            getattr(ctx.reading, "temperature_2m", None),
            ctx.reading.observation_ts,
        )
        if z.z is None:
            return []

        delta = k_hour_delta(
            ctx.reading,
            ctx.history,
            "temperature_2m",
            TEMPERATURE_SHOCK_HOURS,
        )
        if delta is None:
            return []

        abs_z = abs(z.z)
        abs_delta = abs(delta.delta)
        if abs_z < TEMPERATURE_SHOCK_Z or abs_delta < TEMPERATURE_SHOCK_DELTA_C:
            return []

        direction = "warming" if delta.delta > 0 else "cooling"
        signal_values = {
            "value": round(z.value, 3),
            "median": None if z.median is None else round(z.median, 3),
            "scale": None if z.scale is None else round(z.scale, 3),
            "z_score": round(abs_z, 3),
            "signed_z_score": round(z.z, 3),
            "delta_c": round(delta.delta, 3),
            "delta_hours": TEMPERATURE_SHOCK_HOURS,
            "direction": direction,
            "baseline_bucket": z.bucket,
            "baseline_n": z.n,
        }
        return [
            make_candidate(
                ctx,
                event_type="temperature_shock",
                metric="temperature_2m",
                signal_values=signal_values,
                reason=(
                    f"{ctx.reading.city} saw a {direction} temperature shock: "
                    f"{z.value:.1f}C is {abs_z:.1f} sigma from the local-hour "
                    f"baseline and changed {delta.delta:+.1f}C in "
                    f"{TEMPERATURE_SHOCK_HOURS}h."
                ),
                score_inputs={
                    "rarity": min(abs_z / 4.0, 1.0),
                    "magnitude": min(abs_delta / 6.0, 1.0),
                    "compound": 0.5,
                    "confidence": confidence_input(z.confidence),
                },
                detector_name=self.name,
                supporting_readings=[
                    item
                    for item in ctx.history
                    if getattr(item, "id", None) == delta.previous_reading_id
                ],
            )
        ]
