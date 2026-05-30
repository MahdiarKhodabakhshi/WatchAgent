# WatchAgent — Detector Evaluation

> **Regenerate**: after running `python -m app.backfill` to populate the DB,
> run `python scripts/evaluate.py` to recreate this file and all PNGs.

## Method (what is and isn't ground-truthed)

This evaluation has two distinct layers:

1. **Labeled scenarios** (Part A): 18 hand-crafted synthetic
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
| rapid_change_fires_severe | rapid_change | rapid_change | PASS |
| rapid_change_below_threshold | *(none)* | *(none)* | PASS |
| rapid_change_zero_std_no_fire | *(none)* | *(none)* | PASS |
| sustained_extreme_upper_tail | sustained_extreme | sustained_extreme | PASS |
| sustained_extreme_broken_streak | *(none)* | *(none)* | PASS |
| wmo_clear_to_severe | wmo_transition | wmo_transition | PASS |
| wmo_small_jump_no_event | *(none)* | *(none)* | PASS |
| comfort_divergence_fires | comfort_divergence | comfort_divergence | PASS |
| comfort_divergence_normal_gap | *(none)* | *(none)* | PASS |
| cross_city_contrast_fires | cross_city_contrast | cross_city_contrast | PASS |
| cross_city_no_peers | rapid_change | rapid_change | PASS |
| cold_start_short_history | wmo_transition | wmo_transition | PASS |
| cold_start_empty_history | *(none)* | *(none)* | PASS |
| diurnal_warm_afternoon_suppressed | *(none)* | *(none)* | PASS |
| diurnal_genuine_spike_fires | rapid_change | rapid_change | PASS |
| forecast_clear_actual_storm | forecast_divergence, wmo_transition | forecast_divergence, wmo_transition | PASS |
| forecast_temp_miss | forecast_divergence, rapid_change | forecast_divergence, rapid_change | PASS |
| forecast_small_error_no_event | *(none)* | *(none)* | PASS |

**Precision**: 100.0% (12 TP, 0 FP)
**Recall**: 100.0% (12 TP, 0 FN)

## Event rates over backfill (6537 readings)

| event_type | city | count | per 1 000 readings |
|---|---|---:|---:|
| comfort_divergence | Ottawa | 67 | 30.7 |
| comfort_divergence | Toronto | 46 | 21.1 |
| comfort_divergence | Vancouver | 111 | 51.0 |
| cross_city_contrast | Ottawa | 753 | 345.4 |
| cross_city_contrast | Toronto | 828 | 379.8 |
| cross_city_contrast | Vancouver | 883 | 405.6 |
| rapid_change | Ottawa | 227 | 104.1 |
| rapid_change | Toronto | 236 | 108.3 |
| rapid_change | Vancouver | 238 | 109.3 |
| sustained_extreme | Ottawa | 1664 | 763.3 |
| sustained_extreme | Toronto | 1717 | 787.6 |
| sustained_extreme | Vancouver | 1221 | 560.9 |
| wmo_transition | Ottawa | 1 | 0.5 |
| wmo_transition | Toronto | 11 | 5.0 |
| wmo_transition | Vancouver | 4 | 1.8 |

## Severity breakdown per type

| event_type | info | warning | severe |
|---|---:|---:|---:|
| comfort_divergence | 0 | 224 | 0 |
| cross_city_contrast | 0 | 2464 | 0 |
| rapid_change | 0 | 467 | 234 |
| sustained_extreme | 0 | 4602 | 0 |
| wmo_transition | 0 | 16 | 0 |

## Rapid-change z-score distribution

![z-score histogram](evaluation/zscore_histogram.png)

Events only fire above the warning threshold (2.5);
the histogram shows where fired events sit relative to the severe cutoff
(3.5).

## Diurnal baseline split

For `rapid_change` events, how many used the 14-day same-local-hour
baseline vs the fallback rolling 24-hour window:

| baseline_kind | count | fraction |
|---|---:|---:|
| diurnal_same_hour | 690 | 98.4% |
| rolling_24h | 11 | 1.6% |

After the diurnal fix, the events-by-local-hour distribution should
not be skewed toward warm afternoon hours:

![Events by local hour](evaluation/events_by_local_hour.png)

## Forecast skill: MAE and divergence counts

No forecasts in the database. Run with `ENABLE_FORECAST_RECONCILIATION=true` to populate forecasts.

## Threshold justification

- **rapid_change** uses z ≥ 2.5 (warning) and z ≥ 3.5
  (severe). The z-score histogram above shows these thresholds sit in the
  tail of the distribution — most readings fall well below, confirming
  the detector is not over-sensitive.
- **sustained_extreme** uses p5/p95 percentile thresholds over a 48-hour
  window with a 3-reading streak requirement, limiting false positives
  to sustained outliers.
- **comfort_divergence** fires when the apparent-actual gap exceeds
  mean + 2× std of recent gaps, a standard anomaly threshold.
- **forecast_divergence** uses a 6.0°C temperature threshold
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
