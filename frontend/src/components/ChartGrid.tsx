import { useMemo } from "react";

import type { Forecast, Reading, WatchEvent } from "../api/types";
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
  forecasts: Forecast[];
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
  forecast: "var(--line-forecast)",
};

function average(values: Array<number | null>): number | null {
  const numeric = values.filter((value): value is number => value !== null);
  if (numeric.length === 0) {
    return null;
  }
  return numeric.reduce((sum, value) => sum + value, 0) / numeric.length;
}

function buildChartData(readings: Reading[], forecasts: Forecast[]): ChartDatum[] {
  const byTimestamp = new Map<number, Reading[]>();
  readings.forEach((reading) => {
    const ts = new Date(reading.observation_ts).getTime();
    if (Number.isNaN(ts)) {
      return;
    }
    byTimestamp.set(ts, [...(byTimestamp.get(ts) ?? []), reading]);
  });

  const forecastsByTimestamp = new Map<number, Forecast[]>();
  forecasts.forEach((forecast) => {
    const ts = new Date(forecast.target_ts).getTime();
    if (Number.isNaN(ts)) {
      return;
    }
    forecastsByTimestamp.set(ts, [...(forecastsByTimestamp.get(ts) ?? []), forecast]);
  });

  const timestamps = new Set([...byTimestamp.keys(), ...forecastsByTimestamp.keys()]);

  return Array.from(timestamps)
    .map((ts) => ({
      ts,
      rows: byTimestamp.get(ts) ?? [],
      forecastRows: forecastsByTimestamp.get(ts) ?? [],
    }))
    .map(({ ts, rows, forecastRows }) => ({
      ts,
      temperature_2m: rows.length > 0 ? average(rows.map((reading) => reading.temperature_2m)) : null,
      apparent_temperature:
        rows.length > 0 ? average(rows.map((reading) => reading.apparent_temperature)) : null,
      forecast_temperature_2m:
        forecastRows.length > 0
          ? average(forecastRows.map((forecast) => forecast.temperature_2m))
          : null,
      wind_speed_10m: rows.length > 0 ? average(rows.map((reading) => reading.wind_speed_10m)) : null,
      precipitation: rows.length > 0 ? average(rows.map((reading) => reading.precipitation)) : null,
      weather_code: rows.length > 0 ? average(rows.map((reading) => reading.weather_code)) : null,
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
  if (event.metric === "weather_code") {
    return "weather";
  }
  if (event.metric === "wind_speed_10m" || event.metric === "wind_gusts_10m") {
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
  forecasts,
  events,
  isLoading,
  isError,
  selectedEventId,
  onSelectEvent,
  onRetry,
}: ChartGridProps) {
  const chartData = useMemo(() => buildChartData(readings, forecasts), [forecasts, readings]);
  const markers = useMemo(() => buildMarkers(events, readings), [events, readings]);
  const xDomain = useMemo(() => xDomainFor(chartData), [chartData]);
  const hasForecastOverlay = chartData.some((datum) => datum.forecast_temperature_2m !== null);

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
            ...(hasForecastOverlay
              ? [
                  {
                    dataKey: "forecast_temperature_2m" as const,
                    name: "forecast_temperature_2m",
                    color: metricColors.forecast,
                    dashed: true,
                  },
                ]
              : []),
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
