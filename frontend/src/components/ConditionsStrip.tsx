import {
  Cloud,
  CloudFog,
  CloudRain,
  CloudSnow,
  HelpCircle,
  Sun,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useMemo } from "react";

import type { City, DashboardCity, Reading } from "../api/types";
import { CITIES } from "../api/types";
import { describeWmo, type WeatherIcon } from "../design/wmo";
import { fmtPrecip, fmtTemp, fmtTime, fmtWind } from "../lib/format";
import { ErrorState } from "./states/ErrorState";

const weatherIcons: Record<WeatherIcon, LucideIcon> = {
  sun: Sun,
  cloud: Cloud,
  fog: CloudFog,
  rain: CloudRain,
  storm: Zap,
  snow: CloudSnow,
  unknown: HelpCircle,
};

interface ConditionsStripProps {
  city: DashboardCity;
  readings: Reading[];
  isLoading: boolean;
  isError: boolean;
  coldStart: boolean;
  onRetry: () => void;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-panel border border-border bg-surface-2 p-3">
      <div className="label mb-2">{label}</div>
      <div className="mono-num text-sm text-text">{value}</div>
    </div>
  );
}

function ReadingCard({ city, reading, coldStart }: { city: City; reading?: Reading; coldStart: boolean }) {
  if (!reading) {
    return (
      <article className="panel p-4">
        <div className="label mb-3">{city}</div>
        <div className="rounded-panel border border-border bg-surface-2 p-4">
          <div className="mono-num mb-1 text-lg text-text">-- C</div>
          <p className="m-0 text-sm text-text-muted">
            {coldStart ? "Awaiting first reading." : "No readings in this window."}
          </p>
        </div>
      </article>
    );
  }

  const weather = describeWmo(reading.weather_code);
  const Weather = weatherIcons[weather.icon];

  return (
    <article className="panel p-4">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="label mb-2">{city}</div>
          <div className="mono-num text-3xl font-normal leading-none text-text">
            {fmtTemp(reading.temperature_2m)}
          </div>
        </div>
        <div className="flex min-w-24 items-center justify-end gap-2 text-right text-text-muted">
          <Weather aria-hidden="true" size={18} strokeWidth={1.8} />
          <div>
            <div className="text-sm text-text">{weather.label}</div>
            <div className="mono-num text-xs text-text-faint">WMO {reading.weather_code ?? "--"}</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Metric label="Feels" value={fmtTemp(reading.apparent_temperature)} />
        <Metric label="Wind" value={fmtWind(reading.wind_speed_10m)} />
        <Metric label="Precip" value={fmtPrecip(reading.precipitation)} />
        <Metric label="Observed" value={fmtTime(reading.observation_ts)} />
      </div>
    </article>
  );
}

export function ConditionsStrip({
  city,
  readings,
  isLoading,
  isError,
  coldStart,
  onRetry,
}: ConditionsStripProps) {
  const visibleCities = city === "all" ? CITIES : [city];
  const latestByCity = useMemo(() => {
    const latest = new Map<City, Reading>();
    readings.forEach((reading) => {
      if (!latest.has(reading.city)) {
        latest.set(reading.city, reading);
      }
    });
    return latest;
  }, [readings]);

  if (isError) {
    return (
      <section>
        <div className="label mb-3">Current conditions</div>
        <ErrorState resource="Readings" onRetry={onRetry} />
      </section>
    );
  }

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="label">Current conditions</div>
        {isLoading ? <div className="label text-text-faint">Loading</div> : null}
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {visibleCities.map((visibleCity) => (
          <ReadingCard
            key={visibleCity}
            city={visibleCity}
            reading={latestByCity.get(visibleCity)}
            coldStart={coldStart}
          />
        ))}
      </div>
    </section>
  );
}
