import { AlertTriangle, CircleAlert, Info, type LucideIcon } from "lucide-react";

import type { Severity, WatchEvent } from "../api/types";
import { eventTypeLabel, fmtTime, severityLabel, severityVar } from "../lib/format";

const severityIcons: Record<Severity, LucideIcon> = {
  info: Info,
  warning: AlertTriangle,
  severe: CircleAlert,
};

interface EventCardProps {
  event: WatchEvent;
}

export function EventCard({ event }: EventCardProps) {
  const Icon = severityIcons[event.severity];
  const tone = severityVar(event.severity);

  return (
    <article
      className="border border-l-4 border-border bg-surface p-4 [border-bottom-right-radius:8px] [border-top-right-radius:8px]"
      style={{ borderLeftColor: tone }}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="inline-flex h-7 items-center rounded-panel border border-border bg-surface-2 px-2 text-xs uppercase tracking-label text-text-muted">
          {eventTypeLabel(event.event_type)}
        </span>
        <span
          className="inline-flex h-7 items-center gap-1.5 rounded-panel border border-border bg-surface-2 px-2 text-xs uppercase tracking-label"
          style={{ color: tone }}
        >
          <Icon aria-hidden="true" size={13} />
          {severityLabel(event.severity)}
        </span>
      </div>

      <p className="reason-clamp m-0 text-base leading-6 text-text" title={event.reason}>
        {event.reason}
      </p>

      <div className="mt-4 grid grid-cols-2 gap-3 border-t border-border pt-3 text-xs text-text-muted sm:grid-cols-4">
        <div>
          <div className="label mb-1">City</div>
          <div>{event.city}</div>
        </div>
        <div>
          <div className="label mb-1">Event ts</div>
          <div className="mono-num">{fmtTime(event.event_ts)}</div>
        </div>
        <div>
          <div className="label mb-1">Metric</div>
          <div className="mono-num">{event.metric ?? "--"}</div>
        </div>
        <div>
          <div className="label mb-1">Readings</div>
          <div className="mono-num">{event.supporting_reading_ids.length}</div>
        </div>
      </div>
    </article>
  );
}
