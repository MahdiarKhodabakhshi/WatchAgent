# WatchAgent — Detector Evaluation

> **Regenerate**: `python scripts/evaluate.py --source archive --start-date 2023-01-01 --end-date 2025-12-31`. Archive replay is read-only and does not write to
> the live WatchAgent database.

## Method

- Source: **Open-Meteo archive 2023-01-01..2025-12-31**.
- Readings replayed: **78912** across **3288**
  city-days.
- Native replay collapses detector candidates with the same stable dedupe keys,
  enter threshold, and absent-reading resolution used by lifecycle. No live
  application state is touched.
- Fragmentation ratio is raw detector firings divided by lifecycle incidents.
  Without external ground truth, a lifecycle incident is the operational proxy
  for one real incident.
- Open-Meteo archive is observations-only, so forecast-bust archive counts are
  zero unless the `--source db` mode has stored forecasts. Forecast-bust logic is
  covered by unit and labeled tests.

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

| detector_type | incidents | raw_firings | per_1k_readings | per_city_day | fragmentation_ratio |
|---|---:|---:|---:|---:|---:|
| temperature_shock | 19 | 33 | 0.24 | 0.006 | 1.74 |
| pressure_plunge | 35 | 59 | 0.44 | 0.011 | 1.69 |
| warm_spell | 63 | 313 | 0.80 | 0.019 | 4.97 |
| cold_spell | 60 | 321 | 0.76 | 0.018 | 5.35 |
| heavy_rain_burst | 2 | 3 | 0.03 | 0.001 | 1.50 |
| wind_gust_burst | 227 | 769 | 2.88 | 0.069 | 3.39 |
| heat_stress | 45 | 208 | 0.57 | 0.014 | 4.62 |
| cold_stress | 3 | 39 | 0.04 | 0.001 | 13.00 |
| forecast_bust | 0 | 0 | 0.00 | 0.000 | 0.00 |
| spatial_anomaly | 2298 | 15573 | 29.12 | 0.699 | 6.78 |
| OVERALL | 2752 | 17318 | 34.87 | 0.837 | 6.29 |

## Per-City Incident Rates

| city | incidents | per_1k_readings | per_city_day |
|---|---:|---:|---:|
| Ottawa | 817 | 31.06 | 0.745 |
| Toronto | 752 | 28.59 | 0.686 |
| Vancouver | 1183 | 44.97 | 1.079 |

## Severity Breakdown

| detector_type | info | warning | severe |
|---|---:|---:|---:|
| temperature_shock | 0 | 19 | 0 |
| pressure_plunge | 0 | 33 | 2 |
| warm_spell | 0 | 63 | 0 |
| cold_spell | 0 | 60 | 0 |
| heavy_rain_burst | 0 | 1 | 1 |
| wind_gust_burst | 0 | 227 | 0 |
| heat_stress | 0 | 43 | 2 |
| cold_stress | 0 | 3 | 0 |
| forecast_bust | 0 | 0 | 0 |
| spatial_anomaly | 398 | 1900 | 0 |

## Calibration Before/After

| detector_type | before_incidents | before_per_city_day | after_incidents | after_per_city_day |
|---|---:|---:|---:|---:|
| temperature_shock | 93 | 0.028 | 19 | 0.006 |
| pressure_plunge | 188 | 0.057 | 35 | 0.011 |
| warm_spell | 131 | 0.040 | 63 | 0.019 |
| cold_spell | 110 | 0.033 | 60 | 0.018 |
| heavy_rain_burst | 19 | 0.006 | 2 | 0.001 |
| wind_gust_burst | 381 | 0.116 | 227 | 0.069 |
| heat_stress | 95 | 0.029 | 45 | 0.014 |
| cold_stress | 30 | 0.009 | 3 | 0.001 |
| forecast_bust | 0 | 0.000 | 0 | 0.000 |
| spatial_anomaly | 2580 | 0.785 | 2298 | 0.699 |

## Legacy Volume vs Native Incidents

| old_type | replacement | old_raw_events | new_incidents |
|---|---|---:|---:|
| rapid_change | temperature_shock | 8736 | 19 |
| sustained_extreme | warm_spell + cold_spell | 50615 | 123 |
| comfort_divergence | heat_stress + cold_stress | 4295 | 48 |
| cross_city_contrast | spatial_anomaly | 26511 | 2298 |
| forecast_divergence | forecast_bust | 0 | 0 |
| wmo_transition | supporting evidence only | 119 | 0 |
| fun_fact | retired from primary feed | 4801 | 0 |
| *(none)* | pressure_plunge | 0 | 35 |
| *(none)* | heavy_rain_burst | 0 | 2 |
| *(none)* | wind_gust_burst | 0 | 227 |

## Calibration Changes Applied

| detector | change | rationale |
|---|---|---|
| temperature_shock | z 2.5 -> 3.0; delta 4C -> 5C | Reduce routine swings while preserving diurnal z + rate logic. |
| warm/cold spell | z 2.5 -> 3.0 | Spell incidents should be uncommon persistent tails, not every shoulder. |
| pressure_plunge | fall 4hPa -> 6hPa; wind rise 5 -> 8 km/h; gust confirm 50 -> 60 km/h | Keep only stronger storm corroboration. |
| heavy_rain_burst | minimum 10mm/h -> 15mm/h | Move closer to high-impact rain while below the 25mm ECCC anchor. |
| wind_gust_burst | z 2.8 -> 3.2; gust anchor 90 unchanged | Prefer climatology-rare gusts unless an ECCC-scale gust occurs. |
| heat_stress | Humidex 35 -> 38 | Avoid long seasonal discomfort runs; keep Humidex 40 as anchor. |
| cold_stress | wind chill -25 -> -30 | Align with ECCC extreme-cold orientation and reduce mild winter noise. |
| forecast_bust | normalized error 2.0 -> 2.5 | Require clearer surprise over global rolling MAE. |
| spatial_anomaly | peer z-gap 3.0 -> 3.5 | Reduce cross-city background differences after z-normalization. |
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
  keeps the simple global rolling MAE form.
- Optional ECCC weak-label scoring was not run in this pass; the live pipeline
  remains Open-Meteo only.
