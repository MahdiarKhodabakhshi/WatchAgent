import type { EventType, Severity, TimeWindow, WatchEvent } from "../api/types";
import { eventTypeLabel, severityLabel, severityVar, windowLabel } from "../lib/format";
import { EmptyState } from "./states/EmptyState";
import { ErrorState } from "./states/ErrorState";
import { LoadingState } from "./states/LoadingState";
import { EventCard } from "./EventCard";

interface EventFeedProps {
  events: WatchEvent[];
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  windowRange: TimeWindow;
  eventTypes: EventType[];
  severities: Severity[];
  allEventTypes: readonly EventType[];
  allSeverities: readonly Severity[];
  onToggleEventType: (eventType: EventType) => void;
  onToggleSeverity: (severity: Severity) => void;
  onResetFilters: () => void;
  onRetry: () => void;
}

function TypeChip({
  eventType,
  selected,
  onClick,
}: {
  eventType: EventType;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={[
        "h-8 rounded-panel border px-2.5 text-xs uppercase tracking-label transition-colors",
        selected
          ? "border-text-faint bg-surface-2 text-text"
          : "border-border bg-surface text-text-faint hover:border-text-faint hover:text-text-muted",
      ].join(" ")}
    >
      {eventTypeLabel(eventType)}
    </button>
  );
}

function SeverityChip({
  severity,
  selected,
  onClick,
}: {
  severity: Severity;
  selected: boolean;
  onClick: () => void;
}) {
  const tone = severityVar(severity);

  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={[
        "inline-flex h-8 items-center gap-2 rounded-panel border bg-surface px-2.5 text-xs uppercase tracking-label transition-colors",
        selected ? "bg-surface-2" : "text-text-faint hover:border-text-faint",
      ].join(" ")}
      style={selected ? { borderColor: tone, color: tone } : undefined}
    >
      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: tone }} aria-hidden="true" />
      {severityLabel(severity)}
    </button>
  );
}

export function EventFeed({
  events,
  isLoading,
  isFetching,
  isError,
  windowRange,
  eventTypes,
  severities,
  allEventTypes,
  allSeverities,
  onToggleEventType,
  onToggleSeverity,
  onResetFilters,
  onRetry,
}: EventFeedProps) {
  if (isError) {
    return (
      <section>
        <div className="label mb-3">Event feed</div>
        <ErrorState resource="Events" onRetry={onRetry} />
      </section>
    );
  }

  return (
    <section>
      <div className="mb-3 flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <div className="label mb-2">Event feed</div>
          <div className="text-sm text-text-muted">
            <span className="mono-num text-text">{events.length}</span> events in{" "}
            <span className="mono-num text-text">{windowLabel(windowRange)}</span>
            {isFetching && !isLoading ? <span className="ml-2 text-text-faint">refreshing</span> : null}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {allEventTypes.map((eventType) => (
            <TypeChip
              key={eventType}
              eventType={eventType}
              selected={eventTypes.includes(eventType)}
              onClick={() => onToggleEventType(eventType)}
            />
          ))}
          {allSeverities.map((severity) => (
            <SeverityChip
              key={severity}
              severity={severity}
              selected={severities.includes(severity)}
              onClick={() => onToggleSeverity(severity)}
            />
          ))}
          <button
            type="button"
            onClick={onResetFilters}
            className="h-8 rounded-panel border border-border bg-surface px-2.5 text-xs uppercase tracking-label text-text-muted hover:border-text-faint hover:text-text"
          >
            Reset
          </button>
        </div>
      </div>

      {isLoading ? (
        <LoadingState label="Loading events" rows={5} />
      ) : events.length === 0 ? (
        <EmptyState
          title="No events in this window"
          detail="This is a valid quiet state. Broaden the window or filters to inspect more history."
        />
      ) : (
        <div className="space-y-3">
          {events.map((event) => (
            <EventCard key={event.id} event={event} />
          ))}
        </div>
      )}
    </section>
  );
}
