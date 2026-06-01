import {
  CUSTOM_WINDOW_MAX_DAYS,
  CUSTOM_WINDOW_MIN_DAYS,
  type TimeWindow as TimeWindowValue,
} from "../api/types";

interface TimeWindowProps {
  windows: readonly TimeWindowValue[];
  value: TimeWindowValue;
  customDays: number;
  onChange: (value: TimeWindowValue) => void;
  onCustomDaysChange: (value: number) => void;
}

export function TimeWindow({
  windows,
  value,
  customDays,
  onChange,
  onCustomDaysChange,
}: TimeWindowProps) {
  return (
    <section className="panel p-3">
      <div className="label mb-3">Window</div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
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
              {windowValue === "custom" ? "Custom" : windowValue}
            </button>
          );
        })}
      </div>
      {value === "custom" ? (
        <div className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] items-center gap-2">
          <label htmlFor="custom-window-days" className="label">
            Days
          </label>
          <input
            id="custom-window-days"
            type="number"
            min={CUSTOM_WINDOW_MIN_DAYS}
            max={CUSTOM_WINDOW_MAX_DAYS}
            value={customDays}
            onChange={(event) => onCustomDaysChange(Number(event.target.value))}
            className="h-10 min-w-0 rounded-panel border border-border bg-surface-2 px-3 text-sm text-text outline-none focus:border-text-faint"
          />
        </div>
      ) : null}
    </section>
  );
}
