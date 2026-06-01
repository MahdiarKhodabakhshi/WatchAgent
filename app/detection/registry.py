from __future__ import annotations

from app.detection.base import Detector, DetectorContext, EventCandidate
from app.detection.forecast_bust import ForecastBustDetector
from app.detection.heavy_rain_burst import HeavyRainBurstDetector
from app.detection.pressure_plunge import PressurePlungeDetector
from app.detection.spatial_anomaly import SpatialAnomalyDetector
from app.detection.spells import ColdSpellDetector, WarmSpellDetector
from app.detection.stress import ColdStressDetector, HeatStressDetector
from app.detection.temperature_shock import TemperatureShockDetector
from app.detection.wind_gust_burst import WindGustBurstDetector

DEFAULT_DETECTORS: tuple[Detector, ...] = (
    TemperatureShockDetector(),
    PressurePlungeDetector(),
    WarmSpellDetector(),
    ColdSpellDetector(),
    HeavyRainBurstDetector(),
    WindGustBurstDetector(),
    HeatStressDetector(),
    ColdStressDetector(),
    ForecastBustDetector(),
    SpatialAnomalyDetector(),
)


def detect_candidates(
    ctx: DetectorContext,
    detectors: tuple[Detector, ...] = DEFAULT_DETECTORS,
) -> list[EventCandidate]:
    candidates: list[EventCandidate] = []
    for detector in detectors:
        candidates.extend(detector.detect(ctx))
    return candidates
