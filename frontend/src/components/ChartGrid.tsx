import { useMemo } from "react";

import type { Reading, WatchEvent } from "../api/types";
import { EmptyState } from "./states/EmptyState";
import { ErrorState } from "./states/ErrorState";
import { LoadingState } from "./states/LoadingState";
import {
  type ChartDatum,
  type EventMarkerDatum,
  MetricChart,
} from "./MetricChart";

type ChartMetric = "temperature" | "wind" | "precipitation" | "weather";

interface ChartGridProps {
  readings: Reading[];
  events: WatchEvent[];
  isLoading: boolean;
  isError: boolean;
  selectedEventId?: number;
  onSelectEvent: (event: WatchEvent) => void;
  onRetry: () => void;
}

const metricColors = {
  temperature: "var(--line-temp)",
  apparent: "var(--line-apparent)",
  wind: "var(--line-wind)",
  precipitation: "var(--line-precip)",
  weather: "var(--line-forecast)",
};

function average(values: Array<number | null>): number | null {
  const numeric = values.filter((value): value is number => value !== null);
  if (numeric.length === 0) {
    return null;
  }
  return numeric.reduce((sum, value) => sum + value, 0) / numeric.length;
}

function buildChartData(readings: Reading[]): ChartDatum[] {
  const byTimestamp = new Map<number, Reading[]>();
  readings.forEach((reading) => {
    const ts = new Date(reading.observation_ts).getTime();
    if (Number.isNaN(ts)) {
      return;
    }
    byTimestamp.set(ts, [...(byTimestamp.get(ts) ?? []), reading]);
  });

  return Array.from(byTimestamp.entries())
    .map(([ts, rows]) => ({
      ts,
      temperature_2m: average(rows.map((reading) => reading.temperature_2m)),
      apparent_temperature: average(rows.map((reading) => reading.apparent_temperature)),
      wind_speed_10m: average(rows.map((reading) => reading.wind_speed_10m)),
      precipitation: average(rows.map((reading) => reading.precipitation)),
      weather_code: average(rows.map((reading) => reading.weather_code)),
    }))
    .sort((left, right) => left.ts - right.ts);
}

function readingLookup(readings: Reading[]) {
  const byCityTs = new Map<string, Reading>();
  readings.forEach((reading) => {
    const ts = new Date(reading.observation_ts).getTime();
    if (!Number.isNaN(ts)) {
      byCityTs.set(`${reading.city}|${ts}`, reading);
    }
  });
  return byCityTs;
}

function numericSignal(event: WatchEvent, keys: string[]): number | null {
  for (const key of keys) {
    const value = event.signal_values[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return null;
}

function chartMetricForEvent(event: WatchEvent): ChartMetric {
  if (event.metric === "weather_code" || event.event_type === "wmo_transition") {
    return "weather";
  }
  if (event.metric === "wind_speed_10m") {
    return "wind";
  }
  if (event.metric === "precipitation") {
    return "precipitation";
  }
  return "temperature";
}

function markerValueForEvent(
  event: WatchEvent,
  reading: Reading | undefined,
  metric: ChartMetric,
): number | null {
  switch (metric) {
    case "temperature":
      if (event.metric === "apparent_temperature") {
        return (
          reading?.apparent_temperature ??
          numericSignal(event, ["apparent_temperature", "current_value", "value"])
        );
      }
      return (
        reading?.temperature_2m ??
        numericSignal(event, ["temperature_2m", "actual_temp", "current_value", "value"])
      );
    case "wind":
      return reading?.wind_speed_10m ?? numericSignal(event, ["current_value", "value"]);
    case "precipitation":
      return reading?.precipitation ?? numericSignal(event, ["current_value", "value"]);
    case "weather":
      return reading?.weather_code ?? numericSignal(event, ["current_code", "actual_code"]);
  }
}

function buildMarkers(
  events: WatchEvent[],
  readings: Reading[],
): Record<ChartMetric, EventMarkerDatum[]> {
  const byCityTs = readingLookup(readings);
  const markers: Record<ChartMetric, EventMarkerDatum[]> = {
    temperature: [],
    wind: [],
    precipitation: [],
    weather: [],
  };

  events.forEach((event) => {
    const ts = new Date(event.event_ts).getTime();
    if (Number.isNaN(ts)) {
      return;
    }

    const metric = chartMetricForEvent(event);
    const reading = byCityTs.get(`${event.city}|${ts}`);
    const value = markerValueForEvent(event, reading, metric);
    if (value === null) {
      return;
    }

    markers[metric].push({ ts, value, event });
  });

  return markers;
}

function xDomainFor(data: ChartDatum[]): [number, number] {
  const first = data[0]?.ts ?? Date.now() - 60 * 60 * 1000;
  const last = data[data.length - 1]?.ts ?? Date.now();
  if (first === last) {
    return [first - 60 * 60 * 1000, last + 60 * 60 * 1000];
  }
  return [first, last];
}

export function ChartGrid({
  readings,
  events,
  isLoading,
  isError,
  selectedEventId,
  onSelectEvent,
  onRetry,
}: ChartGridProps) {
  const chartData = useMemo(() => buildChartData(readings), [readings]);
  const markers = useMemo(() => buildMarkers(events, readings), [events, readings]);
  const xDomain = useMemo(() => xDomainFor(chartData), [chartData]);

  if (isError) {
    return (
      <section>
        <div className="label mb-3">Metric charts</div>
        <ErrorState resource="Readings" onRetry={onRetry} />
      </section>
    );
  }

  if (isLoading) {
    return (
      <section>
        <div className="label mb-3">Metric charts</div>
        <LoadingState label="Loading charts" rows={4} />
      </section>
    );
  }

  if (chartData.length === 0) {
    return (
      <section>
        <div className="label mb-3">Metric charts</div>
        <EmptyState
          title="No readings in this window"
          detail="Charts will render as soon as matching readings arrive for the selected city and time window."
        />
      </section>
    );
  }

  return (
    <section>
      <div className="label mb-3">Metric charts</div>
      <div className="grid gap-3 xl:grid-cols-2">
        <MetricChart
          title="Temperature"
          unit="C"
          data={chartData}
          xDomain={xDomain}
          lines={[
            {
              dataKey: "temperature_2m",
              name: "temperature_2m",
              color: metricColors.temperature,
            },
            {
              dataKey: "apparent_temperature",
              name: "apparent_temperature",
              color: metricColors.apparent,
              dashed: true,
            },
          ]}
          markers={markers.temperature}
          selectedEventId={selectedEventId}
          onSelectEvent={onSelectEvent}
        />
        <MetricChart
          title="Wind"
          unit="km/h"
          data={chartData}
          xDomain={xDomain}
          lines={[
            {
              dataKey: "wind_speed_10m",
              name: "wind_speed_10m",
              color: metricColors.wind,
            },
          ]}
          markers={markers.wind}
          selectedEventId={selectedEventId}
          onSelectEvent={onSelectEvent}
        />
        <MetricChart
          title="Precipitation"
          unit="mm"
          data={chartData}
          xDomain={xDomain}
          bar={{
            dataKey: "precipitation",
            name: "precipitation",
            color: metricColors.precipitation,
          }}
          markers={markers.precipitation}
          selectedEventId={selectedEventId}
          onSelectEvent={onSelectEvent}
          yDomain={[0, "auto"]}
        />
        <MetricChart
          title="WMO code"
          unit="code"
          data={chartData}
          xDomain={xDomain}
          lines={[
            {
              dataKey: "weather_code",
              name: "weather_code",
              color: metricColors.weather,
              step: true,
            },
          ]}
          markers={markers.weather}
          selectedEventId={selectedEventId}
          onSelectEvent={onSelectEvent}
          showXAxis
          yDomain={[0, "auto"]}
        />
      </div>
    </section>
  );
}
