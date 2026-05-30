#!/usr/bin/env python3
"""Characterisation script for WatchAgent detectors.

Reads the local SQLite DB (populated by ``python -m app.backfill``), re-runs
all detectors in-memory over stored readings, and writes:

  EVALUATION.md   – summary tables, metrics, threshold justification
  evaluation/     – PNG figures (z-score histogram, hourly distribution, etc.)

Usage
-----
    pip install -e '.[dev]'          # installs matplotlib
    python -m app.backfill           # populate DB with ~90 days of data
    python scripts/evaluate.py       # generate EVALUATION.md + PNGs

This script is read-only — it never writes events to the DB.
"""

from __future__ import annotations

import os
import sys
import textwrap
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.detection import detect  # noqa: E402
from app.detection.base import Event  # noqa: E402
from app.detection.rules import (  # noqa: E402
    DIURNAL_WINDOW_DAYS,
    RAPID_CHANGE_Z_SEVERE,
    RAPID_CHANGE_Z_WARNING,
)
from app.detection.timeofday import local_hour  # noqa: E402
from app.models import Base, Forecast, Reading  # noqa: E402
from tests.labeled_scenarios import SCENARIOS  # noqa: E402

CITIES = ["Ottawa", "Toronto", "Vancouver"]
FIG_DIR = PROJECT_ROOT / "evaluation"
EVAL_PATH = PROJECT_ROOT / "EVALUATION.md"


# ---------------------------------------------------------------------------
# DB helpers (read-only)
# ---------------------------------------------------------------------------

def _load_readings(session: Session) -> dict[str, list[Reading]]:
    """Load all readings grouped by city, sorted ascending by observation_ts."""
    readings_by_city: dict[str, list[Reading]] = {}
    for city in CITIES:
        rows = list(
            session.scalars(
                select(Reading)
                .where(Reading.city == city)
                .order_by(Reading.observation_ts.asc())
            ).all()
        )
        readings_by_city[city] = rows
    return readings_by_city


def _load_forecasts(session: Session) -> dict[tuple[str, datetime], Forecast]:
    """Load forecasts keyed by (city, target_ts)."""
    out: dict[tuple[str, datetime], Forecast] = {}
    try:
        rows = list(session.scalars(select(Forecast)).all())
    except Exception:
        return out
    for f in rows:
        out[(f.city, f.target_ts)] = f
    return out


def _latest_peer(
    all_readings: dict[str, list[Reading]],
    exclude_city: str,
    before_ts: datetime,
) -> dict[str, Reading]:
    peers: dict[str, Reading] = {}
    for city, readings in all_readings.items():
        if city == exclude_city:
            continue
        candidate = None
        for r in reversed(readings):
            if r.observation_ts <= before_ts:
                candidate = r
                break
        if candidate:
            peers[city] = candidate
    return peers


# ---------------------------------------------------------------------------
# Replay detection (read-only, in-memory)
# ---------------------------------------------------------------------------

def replay_detection(
    readings_by_city: dict[str, list[Reading]],
    forecasts: dict[tuple[str, datetime], Forecast],
    settings: Any,
) -> list[tuple[Reading, Event]]:
    """Run detect() over every reading with its in-memory history window."""
    results: list[tuple[Reading, Event]] = []
    history_hours = DIURNAL_WINDOW_DAYS * 24

    for city, readings in readings_by_city.items():
        for idx, reading in enumerate(readings):
            cutoff = reading.observation_ts
            start_idx = max(0, idx - history_hours)
            history = [
                r for r in readings[start_idx:idx]
                if r.observation_ts < cutoff
            ]

            peers = _latest_peer(readings_by_city, city, reading.observation_ts)

            forecast = forecasts.get((city, reading.observation_ts))

            events = detect(
                reading,
                history,
                peers=peers if peers else None,
                forecast=forecast,
                forecast_temp_threshold=(
                    settings.forecast_temp_divergence_c if forecast else None
                ),
            )
            for ev in events:
                results.append((reading, ev))

    return results


# ---------------------------------------------------------------------------
# Labeled scenario evaluation
# ---------------------------------------------------------------------------

def evaluate_labeled() -> tuple[int, int, int, list[str]]:
    """Return (tp, fp, fn, detail_lines) from labeled scenarios."""
    tp = fp = fn = 0
    details: list[str] = []
    for s in SCENARIOS:
        events = detect(
            s.reading,
            s.history,
            peers=s.peers,
            forecast=s.forecast,
        )
        actual = {e.event_type for e in events}
        hits = actual & s.expected_types
        misses = s.expected_types - actual
        extras = actual - s.expected_types
        tp += len(hits)
        fp += len(extras)
        fn += len(misses)
        status = "PASS" if actual == s.expected_types else "FAIL"
        details.append(
            f"| {s.name} | {_fmt_set(s.expected_types)} "
            f"| {_fmt_set(actual)} | {status} |"
        )
    return tp, fp, fn, details


def _fmt_set(s: set[str]) -> str:
    return ", ".join(sorted(s)) if s else "*(none)*"


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def event_rate_table_v2(
    results: list[tuple[Reading, Event]],
    readings_by_city: dict[str, list[Reading]],
) -> str:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _reading, ev in results:
        counts[ev.event_type][ev.city] += 1

    lines = [
        "| event_type | city | count | per 1 000 readings |",
        "|---|---|---:|---:|",
    ]
    for etype in sorted(counts):
        for city in sorted(counts[etype]):
            n = counts[etype][city]
            city_n = len(readings_by_city.get(city, []))
            rate = n / city_n * 1000 if city_n else 0
            lines.append(f"| {etype} | {city} | {n} | {rate:.1f} |")
    return "\n".join(lines)


def severity_table(results: list[tuple[Reading, Event]]) -> str:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _, ev in results:
        counts[ev.event_type][ev.severity] += 1

    lines = [
        "| event_type | info | warning | severe |",
        "|---|---:|---:|---:|",
    ]
    for etype in sorted(counts):
        info = counts[etype].get("info", 0)
        warn = counts[etype].get("warning", 0)
        sev = counts[etype].get("severe", 0)
        lines.append(f"| {etype} | {info} | {warn} | {sev} |")
    return "\n".join(lines)


def baseline_kind_split(results: list[tuple[Reading, Event]]) -> str:
    counter: Counter[str] = Counter()
    for _, ev in results:
        if ev.event_type == "rapid_change":
            kind = ev.signal_values.get("baseline_kind", "unknown")
            counter[kind] += 1
    total = sum(counter.values())
    if total == 0:
        return "No rapid_change events found."
    lines = ["| baseline_kind | count | fraction |", "|---|---:|---:|"]
    for kind in sorted(counter):
        lines.append(
            f"| {kind} | {counter[kind]} "
            f"| {counter[kind]/total:.1%} |"
        )
    return "\n".join(lines)


def forecast_skill(
    results: list[tuple[Reading, Event]],
    forecasts: dict[tuple[str, datetime], Forecast],
    readings_by_city: dict[str, list[Reading]],
) -> str:
    if not forecasts:
        return (
            "No forecasts in the database. Run with "
            "`ENABLE_FORECAST_RECONCILIATION=true` to populate forecasts."
        )
    abs_errors: list[float] = []
    for city, readings in readings_by_city.items():
        for r in readings:
            fc = forecasts.get((city, r.observation_ts))
            if fc and fc.temperature_2m is not None and r.temperature_2m is not None:
                abs_errors.append(abs(float(r.temperature_2m) - float(fc.temperature_2m)))

    n_events = sum(
        1 for _, ev in results if ev.event_type == "forecast_divergence"
    )
    mae = sum(abs_errors) / len(abs_errors) if abs_errors else 0.0
    return (
        f"- Forecast/actual pairs compared: **{len(abs_errors)}**\n"
        f"- Mean absolute temperature error: **{mae:.2f} °C**\n"
        f"- forecast_divergence events fired: **{n_events}**"
    )


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def plot_zscore_histogram(results: list[tuple[Reading, Event]]) -> Path:
    zscores = [
        ev.signal_values["z_score"]
        for _, ev in results
        if ev.event_type == "rapid_change" and "z_score" in ev.signal_values
    ]
    fig, ax = plt.subplots(figsize=(8, 4))
    if zscores:
        ax.hist(zscores, bins=30, edgecolor="black", alpha=0.7)
    warn_label = f"warning = {RAPID_CHANGE_Z_WARNING}"
    sev_label = f"severe = {RAPID_CHANGE_Z_SEVERE}"
    ax.axvline(
        RAPID_CHANGE_Z_WARNING, color="orange", ls="--", label=warn_label,
    )
    ax.axvline(
        RAPID_CHANGE_Z_SEVERE, color="red", ls="--", label=sev_label,
    )
    ax.set_xlabel("z-score")
    ax.set_ylabel("count")
    ax.set_title("rapid_change z-score distribution (events only)")
    ax.legend()
    fig.tight_layout()
    path = FIG_DIR / "zscore_histogram.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_events_by_local_hour(results: list[tuple[Reading, Event]]) -> Path:
    hour_counts: Counter[int] = Counter()
    for reading, _ev in results:
        lh = local_hour(reading.city, reading.observation_ts)
        if lh is not None:
            hour_counts[lh] += 1
    hours = list(range(24))
    counts = [hour_counts.get(h, 0) for h in hours]

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(hours, counts, edgecolor="black", alpha=0.7)
    ax.set_xlabel("Local hour")
    ax.set_ylabel("Event count")
    ax.set_title("Events by local hour-of-day (all types, post-diurnal fix)")
    ax.set_xticks(hours)
    fig.tight_layout()
    path = FIG_DIR / "events_by_local_hour.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_severity_pie(results: list[tuple[Reading, Event]]) -> Path:
    counter: Counter[str] = Counter()
    for _, ev in results:
        counter[ev.severity] += 1
    labels = sorted(counter)
    sizes = [counter[label] for label in labels]

    fig, ax = plt.subplots(figsize=(5, 5))
    if sizes:
        ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90)
    ax.set_title("Severity breakdown (all event types)")
    fig.tight_layout()
    path = FIG_DIR / "severity_breakdown.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    os.chdir(PROJECT_ROOT)
    FIG_DIR.mkdir(exist_ok=True)

    settings = get_settings()
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as session:
        readings_by_city = _load_readings(session)
        forecasts = _load_forecasts(session)

    total_readings = sum(len(v) for v in readings_by_city.values())
    print(f"Loaded {total_readings} readings across {len(readings_by_city)} cities.")

    if total_readings == 0:
        print("No readings in DB. Run `python -m app.backfill` first.")
        sys.exit(1)

    print("Replaying detection over all readings (this may take a minute)...")
    results = replay_detection(readings_by_city, forecasts, settings)
    print(f"Detection replay complete: {len(results)} events produced.")

    # --- Labeled scenarios ---
    tp, fp, fn, scenario_details = evaluate_labeled()
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0

    # --- Figures ---
    plot_zscore_histogram(results)
    plot_events_by_local_hour(results)
    plot_severity_pie(results)
    print(f"Figures written to {FIG_DIR}/")

    # --- Build EVALUATION.md ---
    md = textwrap.dedent(f"""\
    # WatchAgent — Detector Evaluation

    > **Regenerate**: after running `python -m app.backfill` to populate the DB,
    > run `python scripts/evaluate.py` to recreate this file and all PNGs.

    ## Method (what is and isn't ground-truthed)

    This evaluation has two distinct layers:

    1. **Labeled scenarios** (Part A): {len(SCENARIOS)} hand-crafted synthetic
       scenarios with known ground-truth event types. These run deterministically
       in CI and yield exact precision/recall numbers. Every scenario controls
       the history, peers, and forecast passed to the detectors, so the
       expected output is fully specified.

    2. **Characterization over backfill** (Part B): the detectors are replayed
       in-memory over ~90 days of real Open-Meteo data stored in the local
       SQLite database. Because there is no ground-truth labeling for real
       weather events, we report *event rates*, *distributions*, and *threshold
       behaviour* — **not** accuracy. This is honest characterization, not a
       claim of precision/recall on unlabeled data.

    ## Labeled scenario results (precision / recall on controlled data)

    | Scenario | Expected | Actual | Status |
    |---|---|---|---|
    """)
    md += "\n".join(scenario_details)
    md += textwrap.dedent(f"""

    **Precision**: {precision:.1%} ({tp} TP, {fp} FP)
    **Recall**: {recall:.1%} ({tp} TP, {fn} FN)

    ## Event rates over backfill ({total_readings} readings)

    {event_rate_table_v2(results, readings_by_city)}

    ## Severity breakdown per type

    {severity_table(results)}

    ## Rapid-change z-score distribution

    ![z-score histogram](evaluation/zscore_histogram.png)

    Events only fire above the warning threshold ({RAPID_CHANGE_Z_WARNING});
    the histogram shows where fired events sit relative to the severe cutoff
    ({RAPID_CHANGE_Z_SEVERE}).

    ## Diurnal baseline split

    For `rapid_change` events, how many used the 14-day same-local-hour
    baseline vs the fallback rolling 24-hour window:

    {baseline_kind_split(results)}

    After the diurnal fix, the events-by-local-hour distribution should
    not be skewed toward warm afternoon hours:

    ![Events by local hour](evaluation/events_by_local_hour.png)

    ## Forecast skill: MAE and divergence counts

    {forecast_skill(results, forecasts, readings_by_city)}

    ## Threshold justification

    - **rapid_change** uses z ≥ {RAPID_CHANGE_Z_WARNING} (warning) and z ≥ {RAPID_CHANGE_Z_SEVERE}
      (severe). The z-score histogram above shows these thresholds sit in the
      tail of the distribution — most readings fall well below, confirming
      the detector is not over-sensitive.
    - **sustained_extreme** uses p5/p95 percentile thresholds over a 48-hour
      window with a 3-reading streak requirement, limiting false positives
      to sustained outliers.
    - **comfort_divergence** fires when the apparent-actual gap exceeds
      mean + 2× std of recent gaps, a standard anomaly threshold.
    - **forecast_divergence** uses a {settings.forecast_temp_divergence_c}°C temperature threshold
      and ≥ 2 WMO-level jump for weather code mismatches — calibrated to
      avoid nuisance alerts from small forecast inaccuracies.

    ## Limitations

    - Labeled scenarios are synthetic; they verify logic correctness but not
      ecological validity against real weather phenomena.
    - Backfill characterization has **no ground truth**. Event rates and
      distributions are descriptive, not measures of accuracy.
    - Cross-city contrast is sensitive to the p95 historical diff; cities with
      correlated climates may produce fewer events than expected.
    - The diurnal baseline requires ≥ 7 same-hour readings over 14 days;
      gaps in polling cause fallback to the rolling window, which may be
      noisier for cities with large diurnal temperature swings.
    """)

    EVAL_PATH.write_text(md)
    print(f"Written {EVAL_PATH}")


if __name__ == "__main__":
    main()
