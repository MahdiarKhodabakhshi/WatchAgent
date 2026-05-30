import { Activity, Database, RefreshCcw, Server } from "lucide-react";

import type { HealthResponse } from "../api/types";
import { fmtRelative } from "../lib/format";

const STALE_AFTER_MS = 15 * 60 * 1000;

interface HealthBarProps {
  health?: HealthResponse;
  latestPolledAt?: string;
  isLoading: boolean;
  isError: boolean;
  isFetching: boolean;
  onRetry: () => void;
}

function statusState(isError: boolean, latestPolledAt?: string) {
  if (isError) {
    return { label: "API error", tone: "var(--sev-severe)" };
  }

  if (!latestPolledAt) {
    return { label: "Awaiting readings", tone: "var(--text-muted)" };
  }

  const polledAt = new Date(latestPolledAt).getTime();
  if (Number.isNaN(polledAt) || Date.now() - polledAt > STALE_AFTER_MS) {
    return { label: "Stale", tone: "var(--sev-warning)" };
  }

  return { label: "Online", tone: "var(--text-muted)" };
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <div className="mono-num text-sm text-text">{value}</div>
    </div>
  );
}

export function HealthBar({
  health,
  latestPolledAt,
  isLoading,
  isError,
  isFetching,
  onRetry,
}: HealthBarProps) {
  const state = statusState(isError, latestPolledAt);

  return (
    <section className="panel flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex min-w-0 items-center gap-3">
        <div className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-panel border border-border bg-surface-2">
          <Activity aria-hidden="true" size={18} strokeWidth={1.8} />
          {isFetching && !isLoading ? (
            <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-text-faint" />
          ) : null}
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: state.tone }}
              aria-hidden="true"
            />
            <span className="label">{state.label}</span>
          </div>
          <div className="mt-1 truncate text-sm text-text-muted">
            Last reading <span className="mono-num text-text">{fmtRelative(latestPolledAt)}</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-[repeat(3,minmax(7rem,auto))] lg:gap-8">
        <Stat
          label="API status"
          value={isLoading ? "--" : health?.status.toUpperCase() ?? "UNKNOWN"}
        />
        <Stat
          label="Readings"
          value={isLoading ? "--" : (health?.readings_stored ?? 0).toLocaleString()}
        />
        <Stat
          label="Events"
          value={isLoading ? "--" : (health?.events_stored ?? 0).toLocaleString()}
        />
      </div>

      {isError ? (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex h-9 items-center justify-center gap-2 rounded-panel border border-border bg-surface-2 px-3 text-sm text-text hover:border-text-faint"
        >
          <RefreshCcw aria-hidden="true" size={15} />
          Retry
        </button>
      ) : (
        <div className="hidden items-center gap-2 text-text-faint lg:flex">
          <Server aria-hidden="true" size={16} />
          <Database aria-hidden="true" size={16} />
        </div>
      )}
    </section>
  );
}
