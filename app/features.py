from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.detection.timeofday import local_day_of_year, local_hour, local_month

DEFAULT_CLIMATOLOGY_PATH = Path(__file__).resolve().parent / "data" / "climatology.json"
MAD_TO_SIGMA = 1.4826
DEFAULT_PRECIP_WET_THRESHOLD_MM = 0.1
DEFAULT_EMPIRICAL_TAIL_QUANTILE = 99.5
DEFAULT_EMPIRICAL_LOWER_QUANTILE = 0.5
DEFAULT_METRIC_EPSILONS: dict[str, float] = {
    "temperature_2m": 0.5,
    "apparent_temperature": 0.5,
    "dew_point_2m": 0.5,
    "precipitation": 0.1,
    "wind_speed_10m": 1.0,
    "wind_gusts_10m": 1.0,
    "surface_pressure": 0.5,
    "pressure_msl": 0.5,
    "relative_humidity_2m": 1.0,
    "cloud_cover": 5.0,
    "snowfall": 0.1,
    "snow_depth": 0.01,
}


@dataclass(frozen=True)
class RobustZScore:
    city: str
    metric: str
    value: float
    z: float | None
    median: float | None
    mad: float | None
    scale: float | None
    n: int
    bucket: str
    confidence: float


@dataclass(frozen=True)
class WetPrecipitation:
    amount_mm: float
    is_wet: bool
    wet_threshold_mm: float
    wet_amount_percentiles: dict[float, float]
    wet_amount_percentile: float | None
    wet_count: int
    total_count: int
    bucket: str
    confidence: float


@dataclass(frozen=True)
class MetricDelta:
    metric: str
    hours: int
    current_value: float
    previous_value: float
    delta: float
    previous_reading_id: int | None


@dataclass(frozen=True)
class ForecastResidual:
    metric: str
    observed: float
    forecast: float
    residual: float
    rolling_mae: float
    normalized_error: float
    comparison_count: int
    confidence: float


class Climatology:
    def __init__(
        self,
        data: Mapping[str, Any],
        *,
        baseline_variant: str | None = None,
        threshold_variant: str | None = None,
    ) -> None:
        self.data = data
        self.baseline_variant = baseline_variant or (
            "smooth" if isinstance(data.get("smooth_buckets"), Mapping) else "legacy"
        )
        self.min_bucket_n = int(data.get("min_bucket_n", 30))
        self.metric_epsilons = {
            **DEFAULT_METRIC_EPSILONS,
            **{
                str(metric): float(value)
                for metric, value in data.get("metric_epsilons", {}).items()
            },
        }
        precipitation = data.get("precipitation", {})
        if not isinstance(precipitation, Mapping):
            precipitation = {}
        self.wet_threshold_mm = float(
            precipitation.get("wet_threshold_mm", DEFAULT_PRECIP_WET_THRESHOLD_MM)
        )
        resolved_threshold_variant = threshold_variant or self.baseline_variant
        threshold_key = (
            "legacy_empirical_thresholds"
            if resolved_threshold_variant == "legacy"
            else "empirical_thresholds"
        )
        thresholds = data.get(threshold_key, {})
        if not thresholds and threshold_key == "legacy_empirical_thresholds":
            thresholds = data.get("empirical_thresholds", {})
        self.empirical_thresholds = thresholds if isinstance(thresholds, Mapping) else {}
        self.empirical_upper_quantile = float(
            self.empirical_thresholds.get(
                "upper_quantile",
                DEFAULT_EMPIRICAL_TAIL_QUANTILE,
            )
        )
        self.empirical_lower_quantile = float(
            self.empirical_thresholds.get(
                "lower_quantile",
                DEFAULT_EMPIRICAL_LOWER_QUANTILE,
            )
        )

    @classmethod
    def from_path(cls, path: Path | str = DEFAULT_CLIMATOLOGY_PATH) -> Climatology:
        with Path(path).open() as f:
            return cls(json.load(f))

    def z_hod(
        self,
        city: str,
        metric: str,
        value: float | int | None,
        observation_ts: Any,
    ) -> RobustZScore:
        if value is None:
            return self._missing_z(city, metric, value=0.0)

        stats, bucket = self._metric_stats(city, metric, observation_ts)
        if stats is None:
            return self._missing_z(city, metric, value=float(value))

        median = float(stats["median"])
        mad = float(stats["mad"])
        scale = max(float(stats.get("scale", 0.0)), self.metric_epsilons.get(metric, 1.0))
        z = (float(value) - median) / scale
        return RobustZScore(
            city=city,
            metric=metric,
            value=float(value),
            z=z,
            median=median,
            mad=mad,
            scale=scale,
            n=int(stats.get("n", 0)),
            bucket=bucket,
            confidence=_confidence_for_bucket(bucket, int(stats.get("n", 0)), self.min_bucket_n),
        )

    def precipitation_features(
        self,
        city: str,
        precipitation: float | int | None,
        observation_ts: Any,
    ) -> WetPrecipitation:
        amount = 0.0 if precipitation is None else float(precipitation)
        stats, bucket = self._precip_stats(city, observation_ts)
        if stats is None:
            return WetPrecipitation(
                amount_mm=amount,
                is_wet=amount >= self.wet_threshold_mm,
                wet_threshold_mm=self.wet_threshold_mm,
                wet_amount_percentiles={},
                wet_amount_percentile=None,
                wet_count=0,
                total_count=0,
                bucket="missing",
                confidence=0.0,
            )

        percentiles = {
            float(percentile): float(value)
            for percentile, value in stats.get("percentiles", {}).items()
        }
        return WetPrecipitation(
            amount_mm=amount,
            is_wet=amount >= self.wet_threshold_mm,
            wet_threshold_mm=self.wet_threshold_mm,
            wet_amount_percentiles=percentiles,
            wet_amount_percentile=_percentile_bucket(amount, percentiles)
            if amount >= self.wet_threshold_mm
            else None,
            wet_count=int(stats.get("wet_count", 0)),
            total_count=int(stats.get("total_count", 0)),
            bucket=bucket,
            confidence=_confidence_for_bucket(
                bucket, int(stats.get("wet_count", 0)), self.min_bucket_n,
            ),
        )

    def empirical_z_threshold(self, metric: str, tail: str) -> float | None:
        """Return a training-only residual quantile threshold for a metric.

        ``tail`` is ``upper`` for positive anomalies or ``lower`` for negative
        anomalies. Lower thresholds are stored as signed negative z-values.
        """

        stats = self._empirical_metric(metric)
        if stats is None:
            return None
        key = "upper_z" if tail == "upper" else "lower_z"
        value = stats.get(key)
        return None if value is None else float(value)

    def empirical_wet_amount_threshold(self) -> float | None:
        stats = self._empirical_metric("precipitation")
        if stats is None:
            return None
        value = stats.get("wet_amount_mm")
        return None if value is None else float(value)

    def _metric_stats(
        self,
        city: str,
        metric: str,
        observation_ts: Any,
    ) -> tuple[Mapping[str, Any] | None, str]:
        month = local_month(city, observation_ts)
        day = local_day_of_year(city, observation_ts)
        hour = local_hour(city, observation_ts)
        buckets = self.data.get("buckets", {})
        smooth_buckets = self.data.get("smooth_buckets", {})
        fallback = self.data.get("fallbacks", {})

        if self.baseline_variant == "smooth" and day is not None and hour is not None:
            stats = _nested_stats(smooth_buckets, city, day, hour, metric)
            if _has_enough(stats, self.min_bucket_n):
                return stats, "smooth_hod"

        if month is not None and hour is not None:
            stats = _nested_stats(buckets, city, month, hour, metric)
            if _has_enough(stats, self.min_bucket_n):
                return stats, "hod"

        if month is not None:
            stats = _nested_stats(fallback.get("month", {}), city, month, metric=metric)
            if _has_enough(stats, self.min_bucket_n):
                return stats, "month"

        stats = _nested_stats(fallback.get("city", {}), city, metric=metric)
        if stats is not None:
            return stats, "city"
        return None, "missing"

    def _precip_stats(
        self,
        city: str,
        observation_ts: Any,
    ) -> tuple[Mapping[str, Any] | None, str]:
        month = local_month(city, observation_ts)
        day = local_day_of_year(city, observation_ts)
        hour = local_hour(city, observation_ts)
        precipitation = self.data.get("precipitation", {})
        if not isinstance(precipitation, Mapping):
            return None, "missing"

        if self.baseline_variant == "smooth" and day is not None and hour is not None:
            stats = _nested_stats(precipitation.get("smooth_buckets", {}), city, day, hour)
            if _has_enough(stats, self.min_bucket_n, n_key="wet_count"):
                return stats, "smooth_hod"

        if month is not None and hour is not None:
            stats = _nested_stats(precipitation.get("buckets", {}), city, month, hour)
            if _has_enough(stats, self.min_bucket_n, n_key="wet_count"):
                return stats, "hod"

        if month is not None:
            stats = _nested_stats(precipitation.get("fallbacks", {}).get("month", {}), city, month)
            if _has_enough(stats, self.min_bucket_n, n_key="wet_count"):
                return stats, "month"

        stats = _nested_stats(precipitation.get("fallbacks", {}).get("city", {}), city)
        if stats is not None:
            return stats, "city"
        return None, "missing"

    def _empirical_metric(self, metric: str) -> Mapping[str, Any] | None:
        metrics = self.empirical_thresholds.get("metrics", {})
        if not isinstance(metrics, Mapping):
            return None
        stats = metrics.get(metric)
        return stats if isinstance(stats, Mapping) else None

    @staticmethod
    def _missing_z(city: str, metric: str, *, value: float) -> RobustZScore:
        return RobustZScore(
            city=city,
            metric=metric,
            value=value,
            z=None,
            median=None,
            mad=None,
            scale=None,
            n=0,
            bucket="missing",
            confidence=0.0,
        )


@lru_cache
def load_default_climatology() -> Climatology:
    return Climatology.from_path(DEFAULT_CLIMATOLOGY_PATH)


def k_hour_delta(
    reading: Any,
    history: Iterable[Any],
    metric: str,
    hours: int,
    *,
    tolerance: timedelta = timedelta(minutes=45),
) -> MetricDelta | None:
    current = _float_attr(reading, metric)
    if current is None:
        return None

    target_ts = reading.observation_ts
    if target_ts.tzinfo is None:
        return None
    target_ts = target_ts.replace(microsecond=0) - timedelta(hours=hours)

    previous = min(
        (
            item
            for item in history
            if getattr(item, "observation_ts", None) is not None
            and item.observation_ts.tzinfo is not None
            and _float_attr(item, metric) is not None
            and abs(item.observation_ts.replace(microsecond=0) - target_ts) <= tolerance
        ),
        key=lambda item: abs(
            (item.observation_ts.replace(microsecond=0) - target_ts).total_seconds()
        ),
        default=None,
    )
    if previous is None:
        return None

    previous_value = _float_attr(previous, metric)
    if previous_value is None:
        return None
    return MetricDelta(
        metric=metric,
        hours=hours,
        current_value=current,
        previous_value=previous_value,
        delta=current - previous_value,
        previous_reading_id=getattr(previous, "id", None),
    )


def peer_z_values(
    peers: Mapping[str, Any],
    metric: str,
    climatology: Climatology,
) -> dict[str, RobustZScore]:
    values: dict[str, RobustZScore] = {}
    for city, peer in peers.items():
        value = getattr(peer, metric, None)
        ts = getattr(peer, "observation_ts", None)
        if ts is None:
            continue
        values[city] = climatology.z_hod(city, metric, value, ts)
    return values


def rolling_mae(
    metric: str,
    comparison_pairs: Iterable[tuple[Any, Any]],
    *,
    floor: float,
) -> tuple[float, int]:
    errors: list[float] = []
    for observed, forecast in comparison_pairs:
        observed_value = _float_attr(observed, metric)
        forecast_value = _float_attr(forecast, metric)
        if observed_value is not None and forecast_value is not None:
            errors.append(abs(observed_value - forecast_value))
    if not errors:
        return floor, 0
    return max(sum(errors) / len(errors), floor), len(errors)


def forecast_residual(
    reading: Any,
    forecast: Any,
    metric: str,
    comparison_pairs: Iterable[tuple[Any, Any]] = (),
    *,
    mae_floor: float | None = None,
) -> ForecastResidual | None:
    observed = _float_attr(reading, metric)
    forecast_value = _float_attr(forecast, metric)
    if observed is None or forecast_value is None:
        return None

    floor = mae_floor if mae_floor is not None else DEFAULT_METRIC_EPSILONS.get(metric, 1.0)
    mae, comparison_count = rolling_mae(metric, comparison_pairs, floor=floor)
    residual = observed - forecast_value
    return ForecastResidual(
        metric=metric,
        observed=observed,
        forecast=forecast_value,
        residual=residual,
        rolling_mae=mae,
        normalized_error=abs(residual) / mae,
        comparison_count=comparison_count,
        confidence=1.0 if comparison_count else 0.4,
    )


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def mad(values: Sequence[float], center: float | None = None) -> float:
    if not values:
        raise ValueError("mad requires at least one value")
    resolved_center = median(values) if center is None else center
    return median([abs(value - resolved_center) for value in values])


def percentile(values: Sequence[float], percentile_value: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile value must be between 0 and 100")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def robust_stats(values: Sequence[float], *, epsilon: float) -> dict[str, float | int]:
    center = median(values)
    raw_mad = mad(values, center)
    return {
        "n": len(values),
        "median": round(center, 4),
        "mad": round(raw_mad, 4),
        "scale": round(max(MAD_TO_SIGMA * raw_mad, epsilon), 4),
    }


def wet_precipitation_stats(
    all_amounts: Sequence[float],
    *,
    wet_threshold_mm: float = DEFAULT_PRECIP_WET_THRESHOLD_MM,
    percentiles: Sequence[float] = (50, 75, 90, 95, 99),
) -> dict[str, Any]:
    wet_amounts = [amount for amount in all_amounts if amount >= wet_threshold_mm]
    return {
        "total_count": len(all_amounts),
        "wet_count": len(wet_amounts),
        "percentiles": {
            str(p): round(percentile(wet_amounts, p), 4)
            for p in percentiles
            if wet_amounts
        },
    }


def _nested_stats(
    root: Mapping[str, Any],
    city: str,
    month: int | None = None,
    hour: int | None = None,
    metric: str | None = None,
) -> Mapping[str, Any] | None:
    cursor: Any = root.get(city)
    for key in (month, hour, metric):
        if key is None:
            continue
        if not isinstance(cursor, Mapping):
            return None
        cursor = cursor.get(str(key))
    return cursor if isinstance(cursor, Mapping) else None


def _has_enough(
    stats: Mapping[str, Any] | None,
    min_bucket_n: int,
    *,
    n_key: str = "n",
) -> bool:
    return stats is not None and int(stats.get(n_key, 0)) >= min_bucket_n


def _confidence_for_bucket(bucket: str, n: int, min_bucket_n: int) -> float:
    if bucket in {"hod", "smooth_hod"}:
        return 1.0
    if bucket == "month":
        return 0.55
    if bucket == "city":
        return 0.35 if n >= min_bucket_n else 0.2
    return 0.0


def _percentile_bucket(amount: float, percentiles: Mapping[float, float]) -> float | None:
    if not percentiles:
        return None
    for percentile_value in sorted(percentiles):
        if amount <= percentiles[percentile_value]:
            return percentile_value
    return max(percentiles)


def _float_attr(item: Any, metric: str) -> float | None:
    value = getattr(item, metric, None)
    return None if value is None else float(value)
