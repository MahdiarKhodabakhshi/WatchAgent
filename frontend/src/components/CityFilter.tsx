import type { DashboardCity } from "../api/types";
import { cityLabel } from "../lib/format";

interface CityFilterProps {
  cities: readonly DashboardCity[];
  value: DashboardCity;
  onChange: (city: DashboardCity) => void;
}

export function CityFilter({ cities, value, onChange }: CityFilterProps) {
  return (
    <section className="panel p-3">
      <div className="label mb-3">City</div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {cities.map((city) => {
          const selected = city === value;
          return (
            <button
              key={city}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(city)}
              className={[
                "h-10 rounded-panel border px-3 text-xs font-medium uppercase tracking-label transition-colors",
                selected
                  ? "border-text-faint bg-surface-2 text-text"
                  : "border-border bg-surface text-text-muted hover:border-text-faint hover:text-text",
              ].join(" ")}
            >
              {cityLabel(city)}
            </button>
          );
        })}
      </div>
    </section>
  );
}
