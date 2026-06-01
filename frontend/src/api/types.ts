export const CITIES = ["Ottawa", "Toronto", "Vancouver"] as const;
export type City = (typeof CITIES)[number];
export type DashboardCity = City | "all";

export const SEVERITIES = ["info", "warning", "severe"] as const;
export type Severity = (typeof SEVERITIES)[number];

export const EVENT_TYPES = [
  "temperature_shock",
  "pressure_plunge",
  "warm_spell",
  "cold_spell",
  "heavy_rain_burst",
  "wind_gust_burst",
  "heat_stress",
  "cold_stress",
  "forecast_bust",
  "spatial_anomaly",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export const TIME_WINDOWS = ["24h", "7d", "14d", "custom"] as const;
export type TimeWindow = (typeof TIME_WINDOWS)[number];
export const DEFAULT_CUSTOM_WINDOW_DAYS = 30;
export const CUSTOM_WINDOW_MIN_DAYS = 1;
export const CUSTOM_WINDOW_MAX_DAYS = 60;

export interface Reading {
  id: number;
  city: City;
  observation_ts: string;
  polled_at: string;
  temperature_2m: number | null;
  apparent_temperature: number | null;
  precipitation: number | null;
  wind_speed_10m: number | null;
  weather_code: number | null;
  surface_pressure: number | null;
  pressure_msl: number | null;
  relative_humidity_2m: number | null;
  dew_point_2m: number | null;
  wind_gusts_10m: number | null;
  cloud_cover: number | null;
  snowfall: number | null;
  snow_depth: number | null;
}

export interface WatchEvent {
  id: number;
  city: City;
  event_ts: string;
  created_at: string;
  event_type: EventType;
  severity: Severity;
  metric: string | null;
  signal_values: Record<string, unknown>;
  reason: string;
  supporting_reading_ids: number[];
  status: string | null;
  onset_ts: string | null;
  peak_ts: string | null;
  resolved_ts: string | null;
  priority_score: number | null;
  confidence: number | null;
  rarity_percentile: number | null;
  detector_name: string | null;
  detector_version: string | null;
  dedupe_key: string | null;
  related_event_ids: number[] | null;
  evidence: Record<string, unknown> | null;
}

export interface Forecast {
  city: City;
  target_ts: string;
  issued_at: string;
  lead_hours: number;
  temperature_2m: number | null;
  precipitation: number | null;
  wind_speed_10m: number | null;
  weather_code: number | null;
  surface_pressure: number | null;
  pressure_msl: number | null;
  relative_humidity_2m: number | null;
  dew_point_2m: number | null;
  wind_gusts_10m: number | null;
  cloud_cover: number | null;
  snowfall: number | null;
  snow_depth: number | null;
}

export interface HealthResponse {
  status: string;
  readings_stored: number;
  events_stored: number;
}

export interface ReadingsResponse {
  readings: Reading[];
}

export interface EventsResponse {
  events: WatchEvent[];
}

export interface ForecastsResponse {
  forecasts: Forecast[];
}

export function isCity(value: string): value is City {
  return CITIES.includes(value as City);
}

export function isSeverity(value: string): value is Severity {
  return SEVERITIES.includes(value as Severity);
}

export function isEventType(value: string): value is EventType {
  return EVENT_TYPES.includes(value as EventType);
}

export function isTimeWindow(value: string): value is TimeWindow {
  return TIME_WINDOWS.includes(value as TimeWindow);
}
