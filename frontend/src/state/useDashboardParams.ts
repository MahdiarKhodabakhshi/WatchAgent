import { useCallback, useEffect, useMemo, useState } from "react";

import {
  CITIES,
  EVENT_TYPES,
  SEVERITIES,
  TIME_WINDOWS,
  type DashboardCity,
  type EventType,
  type Severity,
  type TimeWindow,
  isCity,
  isEventType,
  isSeverity,
  isTimeWindow,
} from "../api/types";

function orderedSubset<T extends string>(values: T[], order: readonly T[]): T[] {
  const selected = new Set(values);
  return order.filter((value) => selected.has(value));
}

function parseList<T extends string>(
  raw: string | null,
  guard: (value: string) => value is T,
  allValues: readonly T[],
): T[] {
  if (!raw) {
    return [...allValues];
  }

  const parsed = raw
    .split(",")
    .map((value) => value.trim())
    .filter(guard);

  return parsed.length > 0 ? orderedSubset(parsed, allValues) : [...allValues];
}

function allSelected<T extends string>(values: T[], allValues: readonly T[]): boolean {
  return values.length === allValues.length && allValues.every((value) => values.includes(value));
}

function readInitialParams() {
  const search = new URLSearchParams(window.location.search);
  const rawCity = search.get("city");
  const rawWindow = search.get("window");

  return {
    city: rawCity === "all" || (rawCity && isCity(rawCity)) ? (rawCity as DashboardCity) : "all",
    windowRange: rawWindow && isTimeWindow(rawWindow) ? rawWindow : "24h",
    eventTypes: parseList(search.get("types"), isEventType, EVENT_TYPES),
    severities: parseList(search.get("severities"), isSeverity, SEVERITIES),
  };
}

function writeParams(
  city: DashboardCity,
  windowRange: TimeWindow,
  eventTypes: EventType[],
  severities: Severity[],
) {
  const search = new URLSearchParams();

  if (city !== "all") {
    search.set("city", city);
  }
  if (windowRange !== "24h") {
    search.set("window", windowRange);
  }
  if (!allSelected(eventTypes, EVENT_TYPES)) {
    search.set("types", eventTypes.join(","));
  }
  if (!allSelected(severities, SEVERITIES)) {
    search.set("severities", severities.join(","));
  }

  const query = search.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.replaceState(null, "", nextUrl);
}

export function useDashboardParams() {
  const initial = useMemo(readInitialParams, []);
  const [city, setCity] = useState<DashboardCity>(initial.city);
  const [windowRange, setWindowRange] = useState<TimeWindow>(initial.windowRange);
  const [eventTypes, setEventTypes] = useState<EventType[]>(initial.eventTypes);
  const [severities, setSeverities] = useState<Severity[]>(initial.severities);

  useEffect(() => {
    writeParams(city, windowRange, eventTypes, severities);
  }, [city, eventTypes, severities, windowRange]);

  const toggleEventType = useCallback((eventType: EventType) => {
    setEventTypes((current) => {
      const exists = current.includes(eventType);
      const next = exists ? current.filter((value) => value !== eventType) : [...current, eventType];
      return next.length > 0 ? orderedSubset(next, EVENT_TYPES) : [...EVENT_TYPES];
    });
  }, []);

  const toggleSeverity = useCallback((severity: Severity) => {
    setSeverities((current) => {
      const exists = current.includes(severity);
      const next = exists ? current.filter((value) => value !== severity) : [...current, severity];
      return next.length > 0 ? orderedSubset(next, SEVERITIES) : [...SEVERITIES];
    });
  }, []);

  const resetEventFilters = useCallback(() => {
    setEventTypes([...EVENT_TYPES]);
    setSeverities([...SEVERITIES]);
  }, []);

  return {
    city,
    windowRange,
    eventTypes,
    severities,
    setCity,
    setWindowRange,
    toggleEventType,
    toggleSeverity,
    resetEventFilters,
    allCities: ["all", ...CITIES] as const,
    allWindows: TIME_WINDOWS,
    allEventTypes: EVENT_TYPES,
    allSeverities: SEVERITIES,
  };
}
