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
- `raw_to_incident_collapse` is raw detector firings divided by lifecycle
  incidents. It is a deduplication win metric, but it blends instantaneous and
  sustained event types, so read it as an average collapse ratio.
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

| detector_type | incidents | raw_firings | per_1k_readings | per_city_day | raw_to_incident_collapse |
|---|---:|---:|---:|---:|---:|
| temperature_shock | 19 | 33 | 0.24 | 0.006 | 1.74 |
| pressure_plunge | 35 | 59 | 0.44 | 0.011 | 1.69 |
| warm_spell | 63 | 313 | 0.80 | 0.019 | 4.97 |
| cold_spell | 60 | 321 | 0.76 | 0.018 | 5.35 |
| heavy_rain_burst | 19 | 22 | 0.24 | 0.006 | 1.16 |
| wind_gust_burst | 227 | 769 | 2.88 | 0.069 | 3.39 |
| heat_stress | 45 | 208 | 0.57 | 0.014 | 4.62 |
| cold_stress | 30 | 203 | 0.38 | 0.009 | 6.77 |
| forecast_bust | 0 | 0 | 0.00 | 0.000 | 0.00 |
| spatial_anomaly | 1838 | 11690 | 23.29 | 0.559 | 6.36 |
| OVERALL | 2336 | 13618 | 29.60 | 0.710 | 5.83 |

Interpretation:

- Heat/cold stress and warm/cold spell all fire across full seasons:
  heat_stress 45, cold_stress 30, warm_spell 63, cold_spell 60.
- Forecast-bust is zero in archive mode because the Open-Meteo archive has no
  stored forecasts; it remains covered by unit and labeled tests.
- Spatial anomaly is still the largest type at 0.559 incidents/city-day
  (about 3.9 per city-week). It was the only noisy detector surfaced by the
  per-type table, so only that threshold was tightened further.

## Per-City Incident Rates

| city | incidents | per_1k_readings | per_city_day |
|---|---:|---:|---:|
| Ottawa | 727 | 27.64 | 0.663 |
| Toronto | 661 | 25.13 | 0.603 |
| Vancouver | 948 | 36.04 | 0.865 |

## Severity Breakdown

| detector_type | info | warning | severe |
|---|---:|---:|---:|
| temperature_shock | 0 | 19 | 0 |
| pressure_plunge | 0 | 33 | 2 |
| warm_spell | 0 | 63 | 0 |
| cold_spell | 0 | 60 | 0 |
| heavy_rain_burst | 0 | 18 | 1 |
| wind_gust_burst | 0 | 227 | 0 |
| heat_stress | 0 | 43 | 2 |
| cold_stress | 0 | 30 | 0 |
| forecast_bust | 0 | 0 | 0 |
| spatial_anomaly | 219 | 1619 | 0 |

## Calibration Before/After

| detector_type | before_incidents | before_per_city_day | after_incidents | after_per_city_day |
|---|---:|---:|---:|---:|
| temperature_shock | 93 | 0.028 | 19 | 0.006 |
| pressure_plunge | 188 | 0.057 | 35 | 0.011 |
| warm_spell | 131 | 0.040 | 63 | 0.019 |
| cold_spell | 110 | 0.033 | 60 | 0.018 |
| heavy_rain_burst | 19 | 0.006 | 19 | 0.006 |
| wind_gust_burst | 381 | 0.116 | 227 | 0.069 |
| heat_stress | 95 | 0.029 | 45 | 0.014 |
| cold_stress | 30 | 0.009 | 30 | 0.009 |
| forecast_bust | 0 | 0.000 | 0 | 0.000 |
| spatial_anomaly | 2580 | 0.785 | 1838 | 0.559 |

## Legacy Volume vs Native Incidents

| old_type | replacement | old_raw_events | new_incidents |
|---|---|---:|---:|
| rapid_change | temperature_shock | 8736 | 19 |
| sustained_extreme | warm_spell + cold_spell | 50615 | 123 |
| comfort_divergence | heat_stress + cold_stress | 4295 | 75 |
| cross_city_contrast | spatial_anomaly | 26511 | 1838 |
| forecast_divergence | forecast_bust | 0 | 0 |
| wmo_transition | supporting evidence only | 119 | 0 |
| fun_fact | retired from primary feed | 4801 | 0 |
| *(none)* | pressure_plunge | 0 | 35 |
| *(none)* | heavy_rain_burst | 0 | 19 |
| *(none)* | wind_gust_burst | 0 | 227 |

## Known-Event Spot Checks

| documented_event | date | replay_incident | priority | evidence | source |
|---|---|---|---:|---|---|
| Toronto heavy rainfall/flooding | 2024-07-16 | spatial_anomaly / precipitation at 2024-07-16 14:00 UTC | 45.0 | warning; precipitation z=6.0 vs peer median z=0.5 | [City reported more than 100 mm in pockets across Toronto.](https://www.toronto.ca/news/city-of-toronto-provides-an-update-on-response-efforts-following-heavy-rainfall/) |
| Vancouver January deep freeze | 2024-01-12 | cold_spell / temperature_2m at 2024-01-11 21:00 UTC | 50.0 peak candidate | warning; Jan 12 candidates reached z=4.2 to z=7.1 | [ECCC noted wind chills reaching Vancouver's waterfront.](https://www.canada.ca/en/environment-climate-change/services/ten-most-impactful-weather-stories/2024.html) |
| Ottawa severe thunderstorm/outages | 2023-06-26 | spatial_anomaly / precipitation at 2023-06-27 01:00 UTC | 45.0 | warning; precipitation z=50.0 vs peer median z=0.0 | [Thousands lost power; ECCC warned of downpours, hail, wind.](https://ottawa.citynews.ca/2023/06/26/environment-canada-issues-severe-thunderstorm-warning-for-ottawa/) |

## Calibration Changes Applied

| detector | change | rationale |
|---|---|---|
| temperature_shock | z 2.5 -> 3.0; delta 4C -> 5C | Reduce routine swings while preserving diurnal z + rate logic. |
| warm/cold spell | z 2.5 -> 3.0 | Spell incidents should be uncommon persistent tails, not every shoulder. |
| pressure_plunge | fall 4hPa -> 6hPa; wind rise 5 -> 8 km/h; gust confirm 50 -> 60 km/h | Keep only stronger storm corroboration. |
| heavy_rain_burst | kept 10mm/h | Per-type replay showed 15mm/h over-suppressed rain bursts. |
| wind_gust_burst | z 2.8 -> 3.2; gust anchor 90 unchanged | Prefer climatology-rare gusts unless an ECCC-scale gust occurs. |
| heat_stress | Humidex 35 -> 38 | Avoid long seasonal discomfort runs; keep Humidex 40 as anchor. |
| cold_stress | kept wind chill -25 | Per-type replay showed -30 was effectively dead for city-center data. |
| forecast_bust | normalized error 2.0 -> 2.5 | Require clearer surprise over global rolling MAE. |
| spatial_anomaly | peer z-gap 3.0 -> 5.0 | It was the one detector dominating the mix after aggregate calibration. |
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
