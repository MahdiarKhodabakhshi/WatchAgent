import { useQuery } from "@tanstack/react-query";

import { fetchEvents, fetchForecasts, fetchHealth, fetchReadings } from "./client";
import {
  CITIES,
  DEFAULT_CUSTOM_WINDOW_DAYS,
  type City,
  type DashboardCity,
  type EventType,
  type Severity,
  type TimeWindow,
  type WatchEvent,
} from "./types";

const REFRESH_INTERVAL_MS = 30_000;
const STALE_TIME_MS = 25_000;
const DAY_MS = 24 * 60 * 60 * 1000;

export function windowToMs(
  windowRange: TimeWindow,
  customWindowDays = DEFAULT_CUSTOM_WINDOW_DAYS,
): number {
  switch (windowRange) {
    case "24h":
      return DAY_MS;
    case "7d":
      return 7 * DAY_MS;
    case "14d":
      return 14 * DAY_MS;
    case "custom":
      return customWindowDays * DAY_MS;
  }
}

function rangeForWindow(windowRange: TimeWindow, customWindowDays?: number) {
  const end = new Date();
  const start = new Date(end.getTime() - windowToMs(windowRange, customWindowDays));
  return {
    start: start.toISOString(),
    end: end.toISOString(),
  };
}

function inWindow(timestamp: string, windowRange: TimeWindow, customWindowDays?: number): boolean {
  const value = new Date(timestamp).getTime();
  if (Number.isNaN(value)) {
    return false;
  }
  return value >= Date.now() - windowToMs(windowRange, customWindowDays);
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: REFRESH_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
  });
}

export function useReadings(params: {
  city: DashboardCity;
  windowRange: TimeWindow;
  customWindowDays: number;
}) {
  return useQuery({
    queryKey: ["readings", params.city, params.windowRange, params.customWindowDays],
    queryFn: () =>
      fetchReadings({
        city: params.city,
        ...rangeForWindow(params.windowRange, params.customWindowDays),
      }),
    refetchInterval: REFRESH_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
    select: (data) => ({
      readings: data.readings.filter((reading) =>
        inWindow(reading.observation_ts, params.windowRange, params.customWindowDays),
      ),
    }),
  });
}

export function useCrossCityReadings(params: { windowRange: TimeWindow; customWindowDays: number }) {
  return useQuery({
    queryKey: ["readings", "cross-city", params.windowRange, params.customWindowDays],
    queryFn: async () => {
      const range = rangeForWindow(params.windowRange, params.customWindowDays);
      const responses = await Promise.all(
        CITIES.map((city: City) => fetchReadings({ city, ...range })),
      );
      return {
        readings: responses.flatMap((response) => response.readings),
      };
    },
    refetchInterval: REFRESH_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
    select: (data) => ({
      readings: data.readings.filter((reading) =>
        inWindow(reading.observation_ts, params.windowRange, params.customWindowDays),
      ),
    }),
  });
}

export function useEvents(params: {
  city: DashboardCity;
  windowRange: TimeWindow;
  customWindowDays: number;
  eventTypes: EventType[];
  severities: Severity[];
}) {
  return useQuery({
    queryKey: [
      "events",
      params.city,
      params.windowRange,
      params.customWindowDays,
      params.eventTypes.join(","),
      params.severities.join(","),
    ],
    queryFn: () =>
      fetchEvents({
        city: params.city,
        ...rangeForWindow(params.windowRange, params.customWindowDays),
      }),
    refetchInterval: REFRESH_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
    select: (data) => {
      const eventTypes = new Set<EventType>(params.eventTypes);
      const severities = new Set<Severity>(params.severities);
      const events = data.events.filter((event: WatchEvent) => {
        return (
          inWindow(event.event_ts, params.windowRange, params.customWindowDays) &&
          eventTypes.has(event.event_type) &&
          severities.has(event.severity)
        );
      });
      return { events };
    },
  });
}

export function useForecasts(params: {
  city: DashboardCity;
  windowRange: TimeWindow;
  customWindowDays: number;
}) {
  return useQuery({
    queryKey: ["forecasts", params.city, params.windowRange, params.customWindowDays],
    queryFn: () =>
      fetchForecasts({
        city: params.city,
        ...rangeForWindow(params.windowRange, params.customWindowDays),
      }),
    refetchInterval: REFRESH_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
    select: (data) => ({
      forecasts: data.forecasts.filter((forecast) =>
        inWindow(forecast.target_ts, params.windowRange, params.customWindowDays),
      ),
    }),
  });
}
