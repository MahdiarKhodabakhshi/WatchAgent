import type { TimeWindow as TimeWindowValue } from "../api/types";

interface TimeWindowProps {
  windows: readonly TimeWindowValue[];
  value: TimeWindowValue;
  onChange: (value: TimeWindowValue) => void;
}

export function TimeWindow({ windows, value, onChange }: TimeWindowProps) {
  return (
    <section className="panel p-3">
      <div className="label mb-3">Window</div>
      <div className="grid grid-cols-3 gap-2">
        {windows.map((windowValue) => {
          const selected = windowValue === value;
          return (
            <button
              key={windowValue}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(windowValue)}
              className={[
                "h-10 rounded-panel border px-3 text-xs font-medium uppercase tracking-label transition-colors",
                selected
                  ? "border-text-faint bg-surface-2 text-text"
                  : "border-border bg-surface text-text-muted hover:border-text-faint hover:text-text",
              ].join(" ")}
            >
              {windowValue}
            </button>
          );
        })}
      </div>
    </section>
  );
}
