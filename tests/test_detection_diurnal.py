"""Tests for Feature 2: diurnal-aware baselines.

Covers:
1. Warm afternoon not flagged against same-hour baseline (showcase test)
2. Genuine afternoon spike still flagged
3. Falls back to rolling-24h when sparse
4. Local hour respects timezone (Vancouver vs Toronto)
5. Local hour DST boundary
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.detection.rules import (
    DIURNAL_WINDOW_DAYS,
    MIN_SAME_HOUR_SAMPLES,
    RAPID_CHANGE_Z_SEVERE,
    RAPID_CHANGE_Z_WARNING,
    detect_rapid_change,
)
from app.detection.statistics import (
    mean,
    metric_values,
    population_std,
    readings_within_hours,
    same_local_hour_values,
)
from app.detection.timeofday import local_hour
from app.models import Reading

# 19:00 UTC = 15:00 Toronto local (EDT, UTC-4)
BASE_TS = datetime(2026, 5, 27, 19, 0, tzinfo=timezone.utc)


def _make_reading(
    *,
    id: int | None = None,
    city: str = "Toronto",
    observation_ts: datetime | None = None,
    hours_offset: int = 0,
    temperature_2m: float = 20.0,
    apparent_temperature: float | None = None,
    precipitation: float = 0.0,
    wind_speed_10m: float = 10.0,
    weather_code: int = 0,
) -> Reading:
    ts = observation_ts or (BASE_TS + timedelta(hours=hours_offset))
    return Reading(
        id=id,
        city=city,
        observation_ts=ts,
        polled_at=ts + timedelta(minutes=5),
        temperature_2m=temperature_2m,
        apparent_temperature=(
            temperature_2m if apparent_temperature is None else apparent_temperature
        ),
        precipitation=precipitation,
        wind_speed_10m=wind_speed_10m,
        weather_code=weather_code,
    )


def _diurnal_history(
    *,
    city: str = "Toronto",
    days: int = DIURNAL_WINDOW_DAYS,
) -> list[Reading]:
    """Build hourly readings with a step-function diurnal pattern.

    Local afternoon hours 14-16 are warm (~28C with slight variation),
    all other hours are cold (14C). This creates a bimodal distribution
    where the rolling-24h mean is dragged down by the cold hours.
    """
    readings: list[Reading] = []
    for hours_ago in range(1, days * 24 + 1):
        ts = BASE_TS - timedelta(hours=hours_ago)
        lh = local_hour(city, ts)
        if lh is not None and 14 <= lh <= 16:
            temp = 27.0 + (hours_ago % 3)  # 27, 28, 29 cycling
        else:
            temp = 14.0
        readings.append(
            _make_reading(
                id=hours_ago,
                city=city,
                observation_ts=ts,
                temperature_2m=temp,
                precipitation=0.0,
                wind_speed_10m=10.0,
            )
        )
    return readings


class TestWarmAfternoonNotFlagged:
    """Showcase test: a normal warm afternoon should NOT fire under diurnal baseline.

    The step-function history creates warm afternoons (~28C) and cold nights (14C).
    Under rolling-24h, the mean is ~15.75 with std ~4.6, so 28C has z~2.6 (fires).
    Under diurnal same-hour, the mean is ~28 with std ~0.8, so 28C has z~0 (no fire).
    """

    def test_warm_afternoon_not_flagged_against_same_hour_baseline(self) -> None:
        history = _diurnal_history(city="Toronto", days=14)
        current = _make_reading(id=10000, city="Toronto", temperature_2m=28.0)

        target_hr = local_hour("Toronto", current.observation_ts)
        assert target_hr is not None
        same_hour_vals = same_local_hour_values(
            history, "temperature_2m", "Toronto", target_hr,
        )
        assert len(same_hour_vals) >= MIN_SAME_HOUR_SAMPLES

        events = detect_rapid_change(current, history)
        rapid = [e for e in events if e.metric == "temperature_2m"]
        assert len(rapid) == 0, (
            f"Warm afternoon (28.0C) should NOT fire against "
            f"diurnal same-hour baseline (mean {mean(same_hour_vals):.1f}, "
            f"std {population_std(same_hour_vals):.1f})"
        )

    def test_would_fire_under_old_rolling_logic(self) -> None:
        """Verify the same reading WOULD fire under a flat rolling-24h baseline."""
        history = _diurnal_history(city="Toronto", days=14)
        current = _make_reading(id=10000, city="Toronto", temperature_2m=28.0)

        rolling_window = readings_within_hours(current, history, 24)
        rolling_vals = metric_values(rolling_window, "temperature_2m")
        assert len(rolling_vals) >= 12, "Need enough rolling data"

        rolling_mean = mean(rolling_vals)
        rolling_std = population_std(rolling_vals)
        assert rolling_std > 0
        z = abs((28.0 - rolling_mean) / rolling_std)
        assert z >= RAPID_CHANGE_Z_WARNING, (
            f"Expected z >= {RAPID_CHANGE_Z_WARNING} under rolling logic, got {z:.2f}"
        )


class TestGenuineAfternoonSpike:
    """A reading far above the same-hour distribution still fires."""

    def test_genuine_afternoon_spike_still_flagged(self) -> None:
        history = _diurnal_history(city="Toronto", days=14)

        target_hr = local_hour("Toronto", BASE_TS)
        assert target_hr is not None
        same_hour_vals = same_local_hour_values(
            history, "temperature_2m", "Toronto", target_hr,
        )
        spike_temp = (
            mean(same_hour_vals)
            + RAPID_CHANGE_Z_SEVERE * population_std(same_hour_vals)
            + 1.0
        )

        current = _make_reading(
            id=10000, city="Toronto", temperature_2m=round(spike_temp, 1),
        )

        events = detect_rapid_change(current, history)
        rapid = [e for e in events if e.metric == "temperature_2m"]
        assert len(rapid) == 1
        assert rapid[0].severity == "severe"
        assert rapid[0].signal_values["baseline_kind"] == "diurnal_same_hour"
        assert "sigma" in rapid[0].reason
        assert "same-hour mean" in rapid[0].reason


class TestFallbackToRolling:
    """With < MIN_SAME_HOUR_SAMPLES, the detector falls back to rolling-24h."""

    def test_falls_back_to_rolling_when_sparse(self) -> None:
        history = _diurnal_history(city="Toronto", days=2)

        target_hr = local_hour("Toronto", BASE_TS)
        assert target_hr is not None
        same_hour_vals = same_local_hour_values(
            history, "temperature_2m", "Toronto", target_hr,
        )
        assert len(same_hour_vals) < MIN_SAME_HOUR_SAMPLES, (
            f"Expected sparse same-hour data (<{MIN_SAME_HOUR_SAMPLES}), "
            f"got {len(same_hour_vals)}"
        )

        rolling_window = readings_within_hours(
            _make_reading(id=10000, city="Toronto", temperature_2m=28.0),
            history,
            24,
        )
        rolling_vals = metric_values(rolling_window, "temperature_2m")
        rolling_mean = mean(rolling_vals)
        rolling_std = population_std(rolling_vals)
        assert rolling_std > 0

        spike_temp = rolling_mean + RAPID_CHANGE_Z_WARNING * rolling_std + 1.0
        current = _make_reading(
            id=10000, city="Toronto", temperature_2m=round(spike_temp, 1),
        )

        events = detect_rapid_change(current, history)
        rapid = [e for e in events if e.metric == "temperature_2m"]
        assert len(rapid) >= 1
        assert rapid[0].signal_values["baseline_kind"] == "rolling_24h"


class TestLocalHourTimezone:
    """Local hour respects timezone: Vancouver and Toronto differ at the same UTC instant."""

    def test_local_hour_respects_timezone(self) -> None:
        utc_ts = datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc)
        toronto_hour = local_hour("Toronto", utc_ts)
        vancouver_hour = local_hour("Vancouver", utc_ts)

        assert toronto_hour is not None
        assert vancouver_hour is not None
        assert toronto_hour != vancouver_hour
        assert toronto_hour == 16  # EDT = UTC-4
        assert vancouver_hour == 13  # PDT = UTC-7


class TestLocalHourDST:
    """Local hour across a DST transition resolves without error."""

    def test_local_hour_dst_boundary(self) -> None:
        pre_dst = datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc)
        post_dst = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)

        pre_hour = local_hour("Toronto", pre_dst)
        post_hour = local_hour("Toronto", post_dst)

        assert pre_hour is not None
        assert post_hour is not None
        # EST (UTC-5): 06:30 UTC = 01:30 local
        assert pre_hour == 1
        # EDT (UTC-4): 07:30 UTC = 03:30 local (spring forward skips 02:xx)
        assert post_hour == 3
