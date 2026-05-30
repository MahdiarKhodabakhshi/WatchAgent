export const CITIES = ["Ottawa", "Toronto", "Vancouver"] as const;
export type City = (typeof CITIES)[number];
export type DashboardCity = City | "all";

export const SEVERITIES = ["info", "warning", "severe"] as const;
export type Severity = (typeof SEVERITIES)[number];

export const EVENT_TYPES = [
  "rapid_change",
  "sustained_extreme",
  "wmo_transition",
  "comfort_divergence",
  "cross_city_contrast",
  "forecast_divergence",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export const TIME_WINDOWS = ["24h", "7d", "14d"] as const;
export type TimeWindow = (typeof TIME_WINDOWS)[number];

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
