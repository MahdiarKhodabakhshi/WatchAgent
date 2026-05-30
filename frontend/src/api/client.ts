import type {
  DashboardCity,
  EventsResponse,
  HealthResponse,
  ReadingsResponse,
} from "./types";

const DEFAULT_LIMIT = 500;

export class ApiError extends Error {
  constructor(
    readonly resource: string,
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function readJson<T>(path: string, resource: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new ApiError(resource, response.status, `${resource} failed with ${response.status}`);
  }

  return (await response.json()) as T;
}

function pathWithParams(path: string, params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) {
      search.set(key, String(value));
    }
  });

  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

function cityParam(city: DashboardCity): string | undefined {
  return city === "all" ? undefined : city;
}

export function fetchHealth(): Promise<HealthResponse> {
  return readJson<HealthResponse>("/health", "Health");
}

export function fetchReadings(params: {
  city: DashboardCity;
  start?: string;
  end?: string;
  limit?: number;
}): Promise<ReadingsResponse> {
  return readJson<ReadingsResponse>(
    pathWithParams("/readings", {
      city: cityParam(params.city),
      start: params.start,
      end: params.end,
      limit: params.limit ?? DEFAULT_LIMIT,
    }),
    "Readings",
  );
}

export function fetchEvents(params: {
  city: DashboardCity;
  start?: string;
  end?: string;
  limit?: number;
}): Promise<EventsResponse> {
  return readJson<EventsResponse>(
    pathWithParams("/events", {
      city: cityParam(params.city),
      start: params.start,
      end: params.end,
      limit: params.limit ?? DEFAULT_LIMIT,
    }),
    "Events",
  );
}
