import { X } from "lucide-react";

import type { WatchEvent } from "../api/types";
import {
  eventTypeLabel,
  fmtTime,
  metricLabel,
  severityLabel,
  severityVar,
} from "../lib/format";

interface EventDetailProps {
  event?: WatchEvent;
  onClose: () => void;
}

function formatSignalValue(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (Array.isArray(value) || typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-panel border border-border bg-surface-2 p-3">
      <div className="label mb-2">{label}</div>
      <div className="mono-num break-words text-sm text-text">{value}</div>
    </div>
  );
}

export function EventDetail({ event, onClose }: EventDetailProps) {
  if (!event) {
    return null;
  }

  const tone = severityVar(event.severity);
  const baselineKind = event.signal_values.baseline_kind;

  return (
    <div
      className="fixed inset-0 z-20 flex justify-end"
      role="presentation"
      style={{ backgroundColor: "color-mix(in srgb, var(--bg) 70%, transparent)" }}
    >
      <button
        type="button"
        className="absolute inset-0 cursor-default"
        aria-label="Close event detail"
        onClick={onClose}
      />
      <aside
        className="relative z-10 flex h-full w-full max-w-xl flex-col border-l border-border bg-surface"
        aria-label="Event detail"
      >
        <header className="border-b border-border p-5">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <div className="label mb-2">Event detail</div>
              <div className="text-lg leading-7 text-text">{event.reason}</div>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-panel border border-border bg-surface-2 text-text-muted hover:border-text-faint hover:text-text"
              aria-label="Close event detail"
            >
              <X aria-hidden="true" size={17} />
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex h-7 items-center rounded-panel border border-border bg-surface-2 px-2 text-xs uppercase tracking-label text-text-muted">
              {eventTypeLabel(event.event_type)}
            </span>
            <span
              className="inline-flex h-7 items-center rounded-panel border border-border bg-surface-2 px-2 text-xs uppercase tracking-label"
              style={{ color: tone }}
            >
              {severityLabel(event.severity)}
            </span>
            {typeof baselineKind === "string" ? (
              <span className="inline-flex h-7 items-center rounded-panel border border-border bg-surface-2 px-2 text-xs uppercase tracking-label text-text">
                {baselineKind}
              </span>
            ) : null}
            {event.status ? (
              <span className="inline-flex h-7 items-center rounded-panel border border-border bg-surface-2 px-2 text-xs uppercase tracking-label text-text-muted">
                {event.status}
              </span>
            ) : null}
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-5">
          <div className="mb-5 grid grid-cols-2 gap-3">
            <Field label="City" value={event.city} />
            <Field label="Metric" value={event.metric ?? "--"} />
            <Field label="Event ts" value={fmtTime(event.event_ts)} />
            <Field label="Created at" value={fmtTime(event.created_at)} />
            <Field
              label="Priority"
              value={event.priority_score !== null ? event.priority_score.toFixed(1) : "--"}
            />
            <Field label="Onset" value={event.onset_ts ? fmtTime(event.onset_ts) : "--"} />
            <Field label="Peak" value={event.peak_ts ? fmtTime(event.peak_ts) : "--"} />
            <Field
              label="Resolved"
              value={event.resolved_ts ? fmtTime(event.resolved_ts) : "--"}
            />
          </div>

          <section className="mb-5">
            <div className="label mb-3">Signal values</div>
            <div className="grid gap-2">
              {Object.entries(event.signal_values).map(([key, value]) => (
                <div
                  key={key}
                  className="grid gap-2 rounded-panel border border-border bg-surface-2 p-3 sm:grid-cols-[12rem_1fr]"
                >
                  <div className="label">{metricLabel(key)}</div>
                  <div className="mono-num break-words text-sm text-text">
                    {formatSignalValue(value)}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="mb-5">
            <div className="label mb-3">Supporting readings</div>
            <div className="rounded-panel border border-border bg-surface-2 p-3">
              <div className="mono-num break-words text-sm text-text">
                {event.supporting_reading_ids.length > 0
                  ? event.supporting_reading_ids.join(", ")
                  : "--"}
              </div>
            </div>
          </section>

          <section>
            <div className="label mb-3">Reason</div>
            <p className="m-0 rounded-panel border border-border bg-surface-2 p-3 text-sm leading-6 text-text">
              {event.reason}
            </p>
          </section>
        </div>
      </aside>
    </div>
  );
}
