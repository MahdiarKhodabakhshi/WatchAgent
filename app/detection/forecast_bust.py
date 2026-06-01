from __future__ import annotations

from dataclasses import dataclass

from app.detection.base import DetectorContext, EventCandidate
from app.detection.native_common import has_native_history, make_candidate
from app.features import forecast_residual

FORECAST_BUST_K = 2.5
MIN_FORECAST_COMPARISONS = 3
FORECAST_METRIC_FLOORS = {
    "temperature_2m": 1.0,
    "precipitation": 1.0,
    "wind_speed_10m": 3.0,
    "wind_gusts_10m": 5.0,
    "pressure_msl": 1.0,
}


@dataclass(frozen=True)
class ForecastBustDetector:
    name: str = "forecast_bust"
    family: str = "native"

    def detect(self, ctx: DetectorContext) -> list[EventCandidate]:
        if not has_native_history(ctx) or ctx.forecast is None:
            return []

        events: list[EventCandidate] = []
        for metric, floor in FORECAST_METRIC_FLOORS.items():
            residual = forecast_residual(
                ctx.reading,
                ctx.forecast,
                metric,
                ctx.forecast_comparison_pairs,
                mae_floor=floor,
            )
            if residual is None:
                continue
            if residual.comparison_count < MIN_FORECAST_COMPARISONS:
                continue
            if residual.normalized_error < FORECAST_BUST_K:
                continue

            abs_error = abs(residual.residual)
            signal_values = {
                "observed": round(residual.observed, 3),
                "forecast": round(residual.forecast, 3),
                "residual": round(residual.residual, 3),
                "abs_error": round(abs_error, 3),
                "rolling_mae": round(residual.rolling_mae, 3),
                "normalized_error": round(residual.normalized_error, 3),
                "comparison_count": residual.comparison_count,
                "lead_hours": getattr(ctx.forecast, "lead_hours", None),
            }
            events.append(
                make_candidate(
                    ctx,
                    event_type="forecast_bust",
                    metric=metric,
                    signal_values=signal_values,
                    reason=(
                        f"{ctx.reading.city}'s {metric} missed the stored forecast by "
                        f"{abs_error:.1f}, {residual.normalized_error:.1f}x the "
                        "recent global MAE."
                    ),
                    score_inputs={
                        "forecast_surprise": min(residual.normalized_error / 4.0, 1.0),
                        "magnitude": min(residual.normalized_error / 4.0, 1.0),
                        "confidence": residual.confidence,
                    },
                    detector_name=self.name,
                )
            )
        return events
