import type { KeyboardEvent } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { WatchEvent } from "../api/types";
import { fmtChartTime, round, severityVar } from "../lib/format";

export interface ChartDatum {
  ts: number;
  temperature_2m: number | null;
  apparent_temperature: number | null;
  forecast_temperature_2m: number | null;
  wind_speed_10m: number | null;
  precipitation: number | null;
  weather_code: number | null;
}

export interface EventMarkerDatum {
  ts: number;
  value: number;
  event: WatchEvent;
}

interface LineSeries {
  dataKey: keyof ChartDatum;
  name: string;
  color: string;
  dashed?: boolean;
  step?: boolean;
}

interface MetricChartProps {
  title: string;
  unit: string;
  data: ChartDatum[];
  xDomain: [number, number];
  lines?: LineSeries[];
  bar?: {
    dataKey: keyof ChartDatum;
    name: string;
    color: string;
  };
  markers: EventMarkerDatum[];
  showXAxis?: boolean;
  selectedEventId?: number;
  onSelectEvent: (event: WatchEvent) => void;
  yDomain?: [number | "auto", number | "auto"];
}

interface EventMarkerShapeProps {
  cx?: number;
  cy?: number;
  payload?: EventMarkerDatum;
}

function EventMarker({
  shapeProps,
  selectedEventId,
  onSelectEvent,
}: {
  shapeProps: EventMarkerShapeProps;
  selectedEventId?: number;
  onSelectEvent: (event: WatchEvent) => void;
}) {
  const { cx, cy, payload } = shapeProps;
  if (cx === undefined || cy === undefined || payload === undefined) {
    return <g />;
  }

  const marker = payload;
  const selected = marker.event.id === selectedEventId;
  const tone = severityVar(marker.event.severity);

  function selectEvent() {
    onSelectEvent(marker.event);
  }

  function handleKeyDown(event: KeyboardEvent<SVGCircleElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectEvent();
    }
  }

  return (
    <circle
      cx={cx}
      cy={cy}
      r={selected ? 5 : 4}
      fill={tone}
      stroke="var(--bg)"
      strokeWidth={selected ? 2 : 1}
      role="button"
      tabIndex={0}
      aria-label={`${marker.event.severity} ${marker.event.event_type} event`}
      onClick={selectEvent}
      onKeyDown={handleKeyDown}
      className="cursor-pointer"
    />
  );
}

export function MetricChart({
  title,
  unit,
  data,
  xDomain,
  lines = [],
  bar,
  markers,
  showXAxis = false,
  selectedEventId,
  onSelectEvent,
  yDomain,
}: MetricChartProps) {
  return (
    <div className="rounded-panel border border-border bg-surface-2 p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="label">{title}</div>
        <div className="mono-num text-xs text-text-faint">{unit}</div>
      </div>
      <div className="h-48 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
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
              hide={!showXAxis}
            />
            <YAxis
              width={42}
              domain={yDomain}
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
                typeof value === "number" ? round(value) : value,
                String(name),
              ]}
            />
            {lines.map((line) => (
              <Line
                key={String(line.dataKey)}
                type={line.step ? "stepAfter" : "monotone"}
                dataKey={line.dataKey}
                name={line.name}
                stroke={line.color}
                strokeDasharray={line.dashed ? "5 5" : undefined}
                strokeWidth={1.8}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
            {bar ? (
              <Bar
                dataKey={bar.dataKey}
                name={bar.name}
                fill={bar.color}
                barSize={10}
                isAnimationActive={false}
              />
            ) : null}
            <Scatter
              name="Events"
              data={markers}
              dataKey="value"
              isAnimationActive={false}
              shape={(props: unknown) => (
                <EventMarker
                  shapeProps={props as EventMarkerShapeProps}
                  selectedEventId={selectedEventId}
                  onSelectEvent={onSelectEvent}
                />
              )}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
