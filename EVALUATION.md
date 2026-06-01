# WatchAgent — Detector Evaluation

> **Regenerate**: `python3 scripts/evaluate.py --source archive --start-date 2022-01-01 --end-date 2025-12-31`. Archive replay is read-only and does not write to
> the live WatchAgent database.

## Method

- Source: **Open-Meteo archive 2022-01-01..2025-12-31**.
- Baseline artifact: **Open-Meteo Historical Weather API (/v1/archive, ERA5), trained on 2015-01-01..2021-12-31**.
- DS-1 uses an honest train/test split: climatology is fit on the committed
  training artifact, while replay metrics are measured on this later disjoint
  evaluation window. This removes leakage from evaluating thresholds against
  the same years used to define seasonal baselines.
- Climate non-stationarity still matters: a fixed historical baseline can drift
  as city climate, observing systems, and reanalysis behavior change over time.
  The split makes leakage visible; it does not make the baseline timeless.
- Readings replayed: **105192** across **4383**
  city-days.
- Native replay collapses detector candidates with the same stable dedupe keys,
  enter threshold, and absent-reading resolution used by lifecycle. No live
  application state is touched.
- The final native table is the **current after-state** after spatial z-gap was
  raised to 5.0 and the structural own-anomaly gate was added.
- `raw_to_incident_collapse` is raw detector firings divided by lifecycle
  incidents. It is a deduplication win metric, but it blends instantaneous and
  sustained event types, so read it as an average collapse ratio.
- Open-Meteo archive is observations-only. In `--source archive` replay,
  `scripts/evaluate.py` has no historically issued forecast rows to pair with
  observations, so `forecast_bust` is expected to show zero. The detector is
  exercised by `tests/test_native_detectors.py::test_forecast_bust_fires_on_error_over_rolling_mae`,
  by the labeled `forecast_bust_simple_mae` scenario, and by live/`--source db`
  operation when stored forecasts exist.

## Labeled Scenario Results

| Scenario | Expected | Actual | Status |
|---|---|---|---|
| temperature_shock_and_spell | temperature_shock, warm_spell | temperature_shock, warm_spell | PASS |
| heavy_rain_wet_hour_only | heavy_rain_burst | heavy_rain_burst | PASS |
| heavy_rain_dry_hour_never_fires | *(none)* | *(none)* | PASS |
| forecast_bust_simple_mae | forecast_bust, warm_spell | forecast_bust, warm_spell | PASS |
| spatial_anomaly_z_space | spatial_anomaly, warm_spell | spatial_anomaly, warm_spell | PASS |

**Precision**: 100.0% (7 TP, 0 FP)  
**Recall**: 100.0% (7 TP, 0 FN)  
**Mean time to detect**: 0.00 h over 7 labeled onsets

## Final Native Incident Rates

| detector_type | incidents | raw_firings | per_1k_readings | per_city_day | raw_to_incident_collapse |
|---|---:|---:|---:|---:|---:|
| temperature_shock | 21 | 30 | 0.20 | 0.005 | 1.43 |
| pressure_plunge | 52 | 88 | 0.49 | 0.012 | 1.69 |
| warm_spell | 101 | 424 | 0.96 | 0.023 | 4.20 |
| cold_spell | 71 | 442 | 0.67 | 0.016 | 6.23 |
| heavy_rain_burst | 333 | 1607 | 3.17 | 0.076 | 4.83 |
| wind_gust_burst | 334 | 1214 | 3.18 | 0.076 | 3.63 |
| heat_stress | 53 | 253 | 0.50 | 0.012 | 4.77 |
| cold_stress | 70 | 516 | 0.67 | 0.016 | 7.37 |
| forecast_bust | 0 | 0 | 0.00 | 0.000 | 0.00 |
| spatial_anomaly | 86 | 278 | 0.82 | 0.020 | 3.23 |
| OVERALL | 1121 | 4852 | 10.66 | 0.256 | 4.33 |

Interpretation:

- Heat/cold stress and warm/cold spell all remain measurable on the test replay: heat_stress 53, cold_stress 70, warm_spell 101, cold_spell 71.
- Forecast-bust is zero in archive mode because the Open-Meteo archive has observations but not the forecasts issued at those historical times; it remains covered by unit and labeled tests and is active in live DB operation when stored forecasts exist.
- Spatial anomaly compares each city in `z_hod` space against that city's own climatology first, then compares the standardized value to peers. A city must be anomalous in its own right and far from peer z-values; normal-for-Vancouver mildness beside normal-for-Ottawa cold is not an event.
- Spatial anomaly is 86/1121 incidents (7.7%), so the structural own-anomaly gate remains visible in the rate mix.
- Spatial incidents use `city|spatial_anomaly|metric` as their dedupe key, with no timestamp component, so multi-hour contrasts collapse into one incident until lifecycle resolves them.

## Per-City Incident Rates

| city | incidents | per_1k_readings | per_city_day |
|---|---:|---:|---:|
| Ottawa | 276 | 7.87 | 0.189 |
| Toronto | 234 | 6.67 | 0.160 |
| Vancouver | 611 | 17.43 | 0.418 |

## Severity Breakdown

| detector_type | info | warning | severe |
|---|---:|---:|---:|
| temperature_shock | 0 | 21 | 0 |
| pressure_plunge | 0 | 41 | 11 |
| warm_spell | 0 | 82 | 19 |
| cold_spell | 0 | 55 | 16 |
| heavy_rain_burst | 0 | 0 | 333 |
| wind_gust_burst | 0 | 334 | 0 |
| heat_stress | 0 | 44 | 9 |
| cold_stress | 0 | 69 | 1 |
| forecast_bust | 0 | 0 | 0 |
| spatial_anomaly | 0 | 86 | 0 |

## Calibration Before/After

| detector_type | before_incidents | before_per_city_day | after_incidents | after_per_city_day |
|---|---:|---:|---:|---:|
| temperature_shock | 119 | 0.027 | 21 | 0.005 |
| pressure_plunge | 258 | 0.059 | 52 | 0.012 |
| warm_spell | 191 | 0.044 | 101 | 0.023 |
| cold_spell | 171 | 0.039 | 71 | 0.016 |
| heavy_rain_burst | 333 | 0.076 | 333 | 0.076 |
| wind_gust_burst | 516 | 0.118 | 334 | 0.076 |
| heat_stress | 112 | 0.026 | 53 | 0.012 |
| cold_stress | 70 | 0.016 | 70 | 0.016 |
| forecast_bust | 0 | 0.000 | 0 | 0.000 |
| spatial_anomaly | 932 | 0.213 | 86 | 0.020 |

## Legacy Volume vs Native Incidents

| old_type | replacement | old_raw_events | new_incidents |
|---|---|---:|---:|
| rapid_change | temperature_shock | 11566 | 21 |
| sustained_extreme | warm_spell + cold_spell | 67513 | 172 |
| comfort_divergence | heat_stress + cold_stress | 5756 | 123 |
| cross_city_contrast | spatial_anomaly | 35779 | 86 |
| forecast_divergence | forecast_bust | 0 | 0 |
| wmo_transition | supporting evidence only | 167 | 0 |
| fun_fact | retired from primary feed | 6383 | 0 |
| *(none)* | pressure_plunge | 0 | 52 |
| *(none)* | heavy_rain_burst | 0 | 333 |
| *(none)* | wind_gust_burst | 0 | 334 |

## Known-Event Spot Checks

| documented_event | date | replay_incident | priority | evidence | source |
|---|---|---|---:|---|---|
| Toronto heavy rainfall/flooding | 2024-07-16 | heavy_rain_burst at 2024-07-16 17:00 UTC | 67.0 | severe; 6h accumulation trigger reached 11.0 mm in archive data | [City reported more than 100 mm in pockets across Toronto.](https://www.toronto.ca/news/city-of-toronto-provides-an-update-on-response-efforts-following-heavy-rainfall/) |
| Vancouver January deep freeze | 2024-01-12 | cold_spell at 2024-01-11 21:00 UTC | 70.0 | severe; Jan 12 candidates reached z=4.2 to z=7.1 | [ECCC noted wind chills reaching Vancouver's waterfront.](https://www.canada.ca/en/environment-climate-change/services/ten-most-impactful-weather-stories/2024.html) |
| Ottawa severe thunderstorm/outages | 2023-06-26 | heavy_rain_burst at 2023-06-27 02:00 UTC | 66.2 | severe; 6h accumulation trigger reached 10.6 mm in archive data | [Thousands lost power; ECCC warned of downpours, hail, wind.](https://ottawa.citynews.ca/2023/06/26/environment-canada-issues-severe-thunderstorm-warning-for-ottawa/) |

## Calibration Changes Applied

| detector | change | rationale |
|---|---|---|
| temperature_shock | z 2.5 -> 3.0; delta 4C -> 5C | Reduce routine swings while preserving diurnal z + rate logic. |
| warm/cold spell | z 2.5 -> 3.0 | Spell incidents should be uncommon persistent tails, not every shoulder. |
| pressure_plunge | fall 4hPa -> 6hPa; wind rise 5 -> 8 km/h; gust confirm 50 -> 60 km/h | Keep only stronger storm corroboration. |
| heavy_rain_burst | kept 10mm/h | Added 6h accumulation scoring so real flash-flood signals reach severe. |
| wind_gust_burst | z 2.8 -> 3.2; gust anchor 90 unchanged | Prefer climatology-rare gusts unless an ECCC-scale gust occurs. |
| heat_stress | Humidex 35 -> 38 | Avoid long seasonal discomfort runs; keep Humidex 40 as anchor. |
| cold_stress | kept wind chill -25 | Per-type replay showed -30 was effectively dead for city-center data. |
| forecast_bust | normalized error 2.0 -> 2.5 | Require clearer surprise over global rolling MAE. |
| spatial_anomaly | peer z-gap 3.0 -> 5.0; own `|z_hod|` >= 3.0; precipitation removed | It was the one detector dominating the mix, and geography alone is not an event. |
| scoring weights | unchanged | The replay showed trigger volume, not feed ranking, was the rate issue. |

## Diagnostic Figures

![z-score histogram](evaluation/zscore_histogram.png)

![Events by local hour](evaluation/events_by_local_hour.png)

![Severity breakdown](evaluation/severity_breakdown.png)

## Notes

- The old detector volume is raw output because the retired system wrote trigger
  rows directly. The native volume is lifecycle incidents because the feed now
  collapses persistent conditions.
- Forecast-bust lead conditioning remains documented future work; this phase
  keeps the simple global rolling MAE form. The archive replay zero is a data
  availability artifact, not evidence that the detector threshold is broken.
- Optional ECCC weak-label scoring was not run in this pass; the live pipeline
  remains Open-Meteo only.
