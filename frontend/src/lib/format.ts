import type { DashboardCity, EventType, Severity, TimeWindow } from "../api/types";

const timeFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

export function round(value: number, digits = 1): string {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function fmtTemp(value: number | null): string {
  return value === null ? "-- C" : `${round(value)} C`;
}

export function fmtWind(value: number | null): string {
  return value === null ? "-- km/h" : `${round(value)} km/h`;
}

export function fmtPrecip(value: number | null): string {
  return value === null ? "-- mm" : `${round(value)} mm`;
}

export function fmtTime(value: string | null | undefined): string {
  if (!value) {
    return "--";
  }

  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : timeFormatter.format(date);
}

export function fmtRelative(value: string | null | undefined): string {
  if (!value) {
    return "no readings";
  }

  const date = new Date(value).getTime();
  if (Number.isNaN(date)) {
    return "unknown";
  }

  const seconds = Math.max(0, Math.floor((Date.now() - date) / 1000));
  if (seconds < 60) {
    return `${seconds}s ago`;
  }

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }

  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    return `${hours}h ago`;
  }

  return `${Math.floor(hours / 24)}d ago`;
}

export function eventTypeLabel(value: EventType): string {
  return value.replace(/_/g, " ");
}

export function severityLabel(value: Severity): string {
  return value.toUpperCase();
}

export function cityLabel(value: DashboardCity): string {
  return value === "all" ? "All cities" : value;
}

export function windowLabel(value: TimeWindow): string {
  switch (value) {
    case "24h":
      return "24 hours";
    case "7d":
      return "7 days";
    case "14d":
      return "14 days";
  }
}

export function severityVar(value: Severity): string {
  return `var(--sev-${value})`;
}
