import { CITIES, type City, type EventType, type WatchEvent } from "../api/types";
import { eventTypeLabel } from "../lib/format";

interface EventDistributionProps {
  events: WatchEvent[];
  eventTypes: readonly EventType[];
}

function countEvents(events: WatchEvent[]) {
  const counts = new Map<string, number>();
  events.forEach((event) => {
    const key = `${event.city}|${event.event_type}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  return counts;
}

function countFor(counts: Map<string, number>, city: City, eventType: EventType): number {
  return counts.get(`${city}|${eventType}`) ?? 0;
}

export function EventDistribution({ events, eventTypes }: EventDistributionProps) {
  const counts = countEvents(events);
  const maxCount = Math.max(1, ...Array.from(counts.values()));

  return (
    <section className="panel p-4">
      <div className="mb-4 flex items-end justify-between gap-3">
        <div>
          <div className="label mb-2">Event distribution</div>
          <div className="text-sm text-text-muted">
            <span className="mono-num text-text">{events.length}</span> filtered events
          </div>
        </div>
      </div>

      <div className="overflow-x-auto">
        <div className="min-w-[42rem]">
          <div className="grid grid-cols-[12rem_repeat(3,minmax(8rem,1fr))] gap-2 border-b border-border pb-2">
            <div className="label">Type</div>
            {CITIES.map((city) => (
              <div key={city} className="label">
                {city}
              </div>
            ))}
          </div>

          <div className="mt-2 space-y-2">
            {eventTypes.map((eventType) => (
              <div
                key={eventType}
                className="grid grid-cols-[12rem_repeat(3,minmax(8rem,1fr))] items-center gap-2"
              >
                <div className="truncate text-sm text-text-muted">{eventTypeLabel(eventType)}</div>
                {CITIES.map((city) => {
                  const count = countFor(counts, city, eventType);
                  return (
                    <div
                      key={`${city}-${eventType}`}
                      className="relative h-8 overflow-hidden rounded-panel border border-border bg-surface-2"
                    >
                      <div
                        className="absolute inset-y-0 left-0 bg-text-faint"
                        style={{ width: `${(count / maxCount) * 100}%` }}
                      />
                      <div className="relative flex h-full items-center px-2">
                        <span className="mono-num text-sm text-text">{count}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
