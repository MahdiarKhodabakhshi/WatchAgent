import { useQuery } from "@tanstack/react-query";

import { fetchEvents, fetchHealth, fetchReadings } from "./client";
import type { DashboardCity, EventType, Severity, TimeWindow, WatchEvent } from "./types";

const REFRESH_INTERVAL_MS = 30_000;
const STALE_TIME_MS = 25_000;

export function windowToMs(windowRange: TimeWindow): number {
  switch (windowRange) {
    case "24h":
      return 24 * 60 * 60 * 1000;
    case "7d":
      return 7 * 24 * 60 * 60 * 1000;
    case "14d":
      return 14 * 24 * 60 * 60 * 1000;
  }
}

function rangeForWindow(windowRange: TimeWindow) {
  const end = new Date();
  const start = new Date(end.getTime() - windowToMs(windowRange));
  return {
    start: start.toISOString(),
    end: end.toISOString(),
  };
}

function inWindow(timestamp: string, windowRange: TimeWindow): boolean {
  const value = new Date(timestamp).getTime();
  if (Number.isNaN(value)) {
    return false;
  }
  return value >= Date.now() - windowToMs(windowRange);
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: REFRESH_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
  });
}

export function useReadings(params: { city: DashboardCity; windowRange: TimeWindow }) {
  return useQuery({
    queryKey: ["readings", params.city, params.windowRange],
    queryFn: () => fetchReadings({ city: params.city, ...rangeForWindow(params.windowRange) }),
    refetchInterval: REFRESH_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
    select: (data) => ({
      readings: data.readings.filter((reading) => inWindow(reading.observation_ts, params.windowRange)),
    }),
  });
}

export function useEvents(params: {
  city: DashboardCity;
  windowRange: TimeWindow;
  eventTypes: EventType[];
  severities: Severity[];
}) {
  return useQuery({
    queryKey: [
      "events",
      params.city,
      params.windowRange,
      params.eventTypes.join(","),
      params.severities.join(","),
    ],
    queryFn: () => fetchEvents({ city: params.city, ...rangeForWindow(params.windowRange) }),
    refetchInterval: REFRESH_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
    select: (data) => {
      const eventTypes = new Set<EventType>(params.eventTypes);
      const severities = new Set<Severity>(params.severities);
      const events = data.events.filter((event: WatchEvent) => {
        return (
          inWindow(event.event_ts, params.windowRange) &&
          eventTypes.has(event.event_type) &&
          severities.has(event.severity)
        );
      });
      return { events };
    },
  });
}
