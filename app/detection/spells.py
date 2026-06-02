from __future__ import annotations

from dataclasses import dataclass

from app.detection.base import DetectorContext, EventCandidate
from app.detection.native_common import (
    climatology_for,
    confidence_input,
    has_native_history,
    make_candidate,
)

SPELL_Z = 3.0
USE_EMPIRICAL_QUANTILE_GATES = True


@dataclass(frozen=True)
class WarmSpellDetector:
    name: str = "warm_spell"
    family: str = "native"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        return _detect_spell(ctx, event_type="warm_spell", detector_name=self.name, sign=1)


@dataclass(frozen=True)
class ColdSpellDetector:
    name: str = "cold_spell"
    family: str = "native"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        return _detect_spell(ctx, event_type="cold_spell", detector_name=self.name, sign=-1)


def _detect_spell(
    ctx: DetectorContext,
    *,
    event_type: str,
    detector_name: str,
    sign: int,
) -> list[EventCandidate]:
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

    signed_threshold, threshold_source = _signed_threshold(climatology, sign)
    if sign * z.z < sign * signed_threshold:
        return []

    tail = "warm" if sign > 0 else "cold"
    abs_z = abs(z.z)
    signal_values = {
        "value": round(z.value, 3),
        "median": None if z.median is None else round(z.median, 3),
        "z_score": round(abs_z, 3),
        "signed_z_score": round(z.z, 3),
        "threshold_z": round(abs(signed_threshold), 3),
        "signed_threshold_z": round(signed_threshold, 3),
        "threshold_source": threshold_source,
        "threshold_quantile": climatology.empirical_upper_quantile,
        "tail": tail,
        "baseline_bucket": z.bucket,
        "baseline_n": z.n,
    }
    return [
        make_candidate(
            ctx,
            event_type=event_type,
            metric="temperature_2m",
            signal_values=signal_values,
            reason=(
                f"{ctx.reading.city} is in a {tail} temperature spell: "
                f"{z.value:.1f}C is {abs_z:.1f} sigma from the local-hour baseline."
            ),
            score_inputs={
                "rarity": min(abs_z / 4.0, 1.0),
                "magnitude": min(abs_z / 6.0, 1.0),
                "persistence": min(abs_z / 4.0, 1.0),
                "confidence": confidence_input(z.confidence),
            },
            detector_name=detector_name,
        )
    ]


def _signed_threshold(climatology, sign: int) -> tuple[float, str]:
    if not USE_EMPIRICAL_QUANTILE_GATES:
        return sign * SPELL_Z, "fixed_z"
    tail = "upper" if sign > 0 else "lower"
    threshold = climatology.empirical_z_threshold("temperature_2m", tail)
    if threshold is None:
        return sign * SPELL_Z, "fixed_z_fallback"
    return threshold, f"training_{tail}_quantile"
