import { useMemo, useState } from "react";

import { useCrossCityReadings, useEvents, useForecasts, useHealth, useReadings } from "./api/hooks";
import type { Forecast, Reading, WatchEvent } from "./api/types";
import { ChartGrid } from "./components/ChartGrid";
import { CityFilter } from "./components/CityFilter";
import { ConditionsStrip } from "./components/ConditionsStrip";
import { CrossCityCompare } from "./components/CrossCityCompare";
import { EventDetail } from "./components/EventDetail";
import { EventDistribution } from "./components/EventDistribution";
import { EventFeed } from "./components/EventFeed";
import { HealthBar } from "./components/HealthBar";
import { TimeWindow } from "./components/TimeWindow";
import { ColdStartState } from "./components/states/ColdStartState";
import { useDashboardParams } from "./state/useDashboardParams";

const EMPTY_READINGS: Reading[] = [];
const EMPTY_EVENTS: WatchEvent[] = [];
const EMPTY_FORECASTS: Forecast[] = [];

function newestPolledAt(readings: { polled_at: string }[]): string | undefined {
  return readings.reduce<string | undefined>((latest, reading) => {
    if (!latest) {
      return reading.polled_at;
    }

    return new Date(reading.polled_at).getTime() > new Date(latest).getTime()
      ? reading.polled_at
      : latest;
  }, undefined);
}

export default function App() {
  const [selectedEventId, setSelectedEventId] = useState<number | undefined>();
  const params = useDashboardParams();
  const healthQuery = useHealth();
  const readingsQuery = useReadings({
    city: params.city,
    windowRange: params.windowRange,
  });
  const crossCityReadingsQuery = useCrossCityReadings({
    windowRange: params.windowRange,
  });
  const forecastsQuery = useForecasts({
    city: params.city,
    windowRange: params.windowRange,
  });
  const eventsQuery = useEvents({
    city: params.city,
    windowRange: params.windowRange,
    eventTypes: params.eventTypes,
    severities: params.severities,
  });

  const readings = readingsQuery.data?.readings ?? EMPTY_READINGS;
  const crossCityReadings = crossCityReadingsQuery.data?.readings ?? EMPTY_READINGS;
  const forecasts = forecastsQuery.data?.forecasts ?? EMPTY_FORECASTS;
  const events = eventsQuery.data?.events ?? EMPTY_EVENTS;
  const latestPolledAt = useMemo(() => newestPolledAt(readings), [readings]);
  const selectedEvent = useMemo(
    () => events.find((event) => event.id === selectedEventId),
    [events, selectedEventId],
  );
  const coldStart = healthQuery.data?.readings_stored === 0;

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 border-b border-border pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <div className="label mb-2">WatchAgent</div>
          <h1 className="m-0 text-2xl font-normal tracking-normal text-text sm:text-3xl">
            Weather event monitor
          </h1>
        </div>
        <div className="max-w-2xl text-sm leading-6 text-text-muted">
          Read-only view over public API readings and detector events for Ottawa, Toronto, and
          Vancouver.
        </div>
      </header>

      <HealthBar
        health={healthQuery.data}
        latestPolledAt={latestPolledAt}
        isLoading={healthQuery.isPending}
        isError={healthQuery.isError}
        isFetching={healthQuery.isFetching}
        onRetry={() => void healthQuery.refetch()}
      />

      {coldStart ? <ColdStartState /> : null}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.42fr)]">
        <CityFilter cities={params.allCities} value={params.city} onChange={params.setCity} />
        <TimeWindow
          windows={params.allWindows}
          value={params.windowRange}
          onChange={params.setWindowRange}
        />
      </div>

      <ConditionsStrip
        city={params.city}
        readings={readings}
        isLoading={readingsQuery.isPending}
        isError={readingsQuery.isError}
        coldStart={coldStart}
        onRetry={() => void readingsQuery.refetch()}
      />

      <ChartGrid
        readings={readings}
        forecasts={forecasts}
        events={events}
        isLoading={readingsQuery.isPending}
        isError={readingsQuery.isError}
        selectedEventId={selectedEventId}
        onSelectEvent={(event) => setSelectedEventId(event.id)}
        onRetry={() => void readingsQuery.refetch()}
      />

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(24rem,0.45fr)]">
        <CrossCityCompare
          readings={crossCityReadings}
          isLoading={crossCityReadingsQuery.isPending}
          isError={crossCityReadingsQuery.isError}
          onRetry={() => void crossCityReadingsQuery.refetch()}
        />
        <EventDistribution events={events} eventTypes={params.allEventTypes} />
      </div>

      <EventFeed
        events={events}
        isLoading={eventsQuery.isPending}
        isFetching={eventsQuery.isFetching}
        isError={eventsQuery.isError}
        windowRange={params.windowRange}
        eventTypes={params.eventTypes}
        severities={params.severities}
        allEventTypes={params.allEventTypes}
        allSeverities={params.allSeverities}
        onToggleEventType={params.toggleEventType}
        onToggleSeverity={params.toggleSeverity}
        onResetFilters={params.resetEventFilters}
        onRetry={() => void eventsQuery.refetch()}
        selectedEventId={selectedEventId}
        onSelectEvent={(event) => setSelectedEventId(event.id)}
      />

      <EventDetail event={selectedEvent} onClose={() => setSelectedEventId(undefined)} />
    </main>
  );
}
