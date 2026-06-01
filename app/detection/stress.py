from __future__ import annotations

import math
from dataclasses import dataclass

from app.detection.base import DetectorContext, EventCandidate
from app.detection.native_common import (
    climatology_for,
    confidence_input,
    has_native_history,
    make_candidate,
    numeric_attr,
)

HEAT_STRESS_HUMIDEX = 38.0
STRONG_HEAT_HUMIDEX = 40.0
COLD_STRESS_WIND_CHILL = -30.0
STRONG_COLD_WIND_CHILL = -35.0
MIN_WIND_CHILL_KMH = 4.8


@dataclass(frozen=True)
class HeatStressDetector:
    name: str = "heat_stress"
    family: str = "native"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        if not has_native_history(ctx):
            return []

        temperature = numeric_attr(ctx.reading, "temperature_2m")
        dew_point = numeric_attr(ctx.reading, "dew_point_2m")
        if temperature is None or dew_point is None:
            return []

        humidex_value = humidex(temperature, dew_point)
        if humidex_value < HEAT_STRESS_HUMIDEX:
            return []

        z = climatology_for(ctx).z_hod(
            ctx.reading.city,
            "temperature_2m",
            temperature,
            ctx.reading.observation_ts,
        )
        signal_values = {
            "humidex": round(humidex_value, 3),
            "gap": round(humidex_value - HEAT_STRESS_HUMIDEX, 3),
            "temperature_2m": round(temperature, 3),
            "dew_point_2m": round(dew_point, 3),
            "z_score": None if z.z is None else round(max(z.z, 0.0), 3),
            "baseline_bucket": z.bucket,
        }
        return [
            make_candidate(
                ctx,
                event_type="heat_stress",
                metric="humidex",
                signal_values=signal_values,
                reason=(
                    f"{ctx.reading.city} has heat stress conditions: Humidex "
                    f"{humidex_value:.1f} from {temperature:.1f}C air and "
                    f"{dew_point:.1f}C dew point."
                ),
                score_inputs={
                    "rarity": min(max(z.z or 0.0, 0.0) / 4.0, 1.0),
                    "magnitude": min(
                        (humidex_value - HEAT_STRESS_HUMIDEX)
                        / (STRONG_HEAT_HUMIDEX - HEAT_STRESS_HUMIDEX),
                        1.0,
                    ),
                    "compound": 1.0,
                    "persistence": 0.5,
                    "confidence": confidence_input(z.confidence),
                },
                detector_name=self.name,
            )
        ]


@dataclass(frozen=True)
class ColdStressDetector:
    name: str = "cold_stress"
    family: str = "native"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        if not has_native_history(ctx):
            return []

        temperature = numeric_attr(ctx.reading, "temperature_2m")
        wind_speed = numeric_attr(ctx.reading, "wind_speed_10m")
        if temperature is None or wind_speed is None:
            return []

        chill = wind_chill(temperature, wind_speed)
        if chill is None or chill > COLD_STRESS_WIND_CHILL:
            return []

        z = climatology_for(ctx).z_hod(
            ctx.reading.city,
            "temperature_2m",
            temperature,
            ctx.reading.observation_ts,
        )
        cold_z = abs(min(z.z or 0.0, 0.0))
        signal_values = {
            "wind_chill": round(chill, 3),
            "gap": round(COLD_STRESS_WIND_CHILL - chill, 3),
            "temperature_2m": round(temperature, 3),
            "wind_speed_10m": round(wind_speed, 3),
            "z_score": round(cold_z, 3),
            "signed_z_score": None if z.z is None else round(z.z, 3),
            "baseline_bucket": z.bucket,
        }
        return [
            make_candidate(
                ctx,
                event_type="cold_stress",
                metric="wind_chill",
                signal_values=signal_values,
                reason=(
                    f"{ctx.reading.city} has cold stress conditions: wind chill "
                    f"{chill:.1f}C with {temperature:.1f}C air and "
                    f"{wind_speed:.1f} km/h wind."
                ),
                score_inputs={
                    "rarity": min(cold_z / 4.0, 1.0),
                    "magnitude": min(
                        (COLD_STRESS_WIND_CHILL - chill)
                        / (COLD_STRESS_WIND_CHILL - STRONG_COLD_WIND_CHILL),
                        1.0,
                    ),
                    "compound": 1.0,
                    "persistence": 0.5,
                    "confidence": confidence_input(z.confidence),
                },
                detector_name=self.name,
            )
        ]


def humidex(temperature_c: float, dew_point_c: float) -> float:
    vapour_pressure_hpa = 6.11 * math.exp(
        5417.7530 * (1 / 273.16 - 1 / (273.15 + dew_point_c))
    )
    return temperature_c + 0.5555 * (vapour_pressure_hpa - 10)


def wind_chill(temperature_c: float, wind_kmh: float) -> float | None:
    if temperature_c > 10 or wind_kmh <= MIN_WIND_CHILL_KMH:
        return None
    wind_power = wind_kmh ** 0.16
    return (
        13.12
        + 0.6215 * temperature_c
        - 11.37 * wind_power
        + 0.3965 * temperature_c * wind_power
    )
