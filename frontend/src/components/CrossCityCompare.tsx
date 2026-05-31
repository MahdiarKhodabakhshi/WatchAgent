import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { CITIES, type City, type Reading } from "../api/types";
import { fmtChartTime, metricLabel, round } from "../lib/format";
import { EmptyState } from "./states/EmptyState";
import { ErrorState } from "./states/ErrorState";
import { LoadingState } from "./states/LoadingState";

type CompareMetric =
  | "temperature_2m"
  | "apparent_temperature"
  | "wind_speed_10m"
  | "precipitation";

type CompareDatum = {
  ts: number;
} & Partial<Record<City, number | null>>;

interface CrossCityCompareProps {
  readings: Reading[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
}

const metrics: Array<{ key: CompareMetric; unit: string }> = [
  { key: "temperature_2m", unit: "C" },
  { key: "apparent_temperature", unit: "C" },
  { key: "wind_speed_10m", unit: "km/h" },
  { key: "precipitation", unit: "mm" },
];

const cityLineColors: Record<City, string> = {
  Ottawa: "var(--line-temp)",
  Toronto: "var(--line-wind)",
  Vancouver: "var(--line-precip)",
};

function buildCompareData(readings: Reading[], metric: CompareMetric): CompareDatum[] {
  const byTimestamp = new Map<number, CompareDatum>();
  readings.forEach((reading) => {
    const ts = new Date(reading.observation_ts).getTime();
    const value = reading[metric];
    if (Number.isNaN(ts)) {
      return;
    }

    const datum = byTimestamp.get(ts) ?? { ts };
    datum[reading.city] = value;
    byTimestamp.set(ts, datum);
  });

  return Array.from(byTimestamp.values()).sort((left, right) => left.ts - right.ts);
}

function xDomainFor(data: CompareDatum[]): [number, number] {
  const first = data[0]?.ts ?? Date.now() - 60 * 60 * 1000;
  const last = data[data.length - 1]?.ts ?? Date.now();
  if (first === last) {
    return [first - 60 * 60 * 1000, last + 60 * 60 * 1000];
  }
  return [first, last];
}

export function CrossCityCompare({
  readings,
  isLoading,
  isError,
  onRetry,
}: CrossCityCompareProps) {
  const [metric, setMetric] = useState<CompareMetric>("temperature_2m");
  const selectedMetric = metrics.find((item) => item.key === metric) ?? metrics[0];
  const data = useMemo(() => buildCompareData(readings, metric), [metric, readings]);
  const xDomain = useMemo(() => xDomainFor(data), [data]);

  if (isError) {
    return (
      <section>
        <div className="label mb-3">Cross-city compare</div>
        <ErrorState resource="Cross-city readings" onRetry={onRetry} />
      </section>
    );
  }

  if (isLoading) {
    return (
      <section>
        <div className="label mb-3">Cross-city compare</div>
        <LoadingState label="Loading comparison" rows={4} />
      </section>
    );
  }

  return (
    <section className="panel p-4">
      <div className="mb-3 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <div className="label mb-2">Cross-city compare</div>
          <div className="text-sm text-text-muted">
            <span className="mono-num text-text">{readings.length}</span> readings,{" "}
            {metricLabel(metric)}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {metrics.map((item) => {
            const selected = item.key === metric;
            return (
              <button
                key={item.key}
                type="button"
                aria-pressed={selected}
                onClick={() => setMetric(item.key)}
                className={[
                  "h-8 rounded-panel border px-2.5 text-xs uppercase tracking-label transition-colors",
                  selected
                    ? "border-text-faint bg-surface-2 text-text"
                    : "border-border bg-surface text-text-faint hover:border-text-faint hover:text-text-muted",
                ].join(" ")}
              >
                {metricLabel(item.key)}
              </button>
            );
          })}
        </div>
      </div>

      {data.length === 0 ? (
        <EmptyState
          title="No cross-city readings"
          detail="Comparison appears when the selected time window has readings for one or more cities."
        />
      ) : (
        <div className="h-72 w-full rounded-panel border border-border bg-surface-2 p-3">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" vertical={false} />
              <XAxis
                dataKey="ts"
                type="number"
                scale="time"
                domain={xDomain}
                tickFormatter={fmtChartTime}
                tick={{ fill: "var(--text-faint)", fontSize: 11 }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
              />
              <YAxis
                width={42}
                tickFormatter={(value) => round(Number(value), 0)}
                tick={{ fill: "var(--text-faint)", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                cursor={{ stroke: "var(--border)" }}
                contentStyle={{
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  color: "var(--text)",
                }}
                labelFormatter={(value) => fmtChartTime(Number(value))}
                formatter={(value, name) => [
                  typeof value === "number" ? `${round(value)} ${selectedMetric.unit}` : value,
                  String(name),
                ]}
              />
              {CITIES.map((city) => (
                <Line
                  key={city}
                  type="monotone"
                  dataKey={city}
                  name={city}
                  stroke={cityLineColors[city]}
                  strokeWidth={1.8}
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}
