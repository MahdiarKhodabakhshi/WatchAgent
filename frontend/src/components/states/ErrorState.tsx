import { AlertCircle, RefreshCcw } from "lucide-react";

interface ErrorStateProps {
  resource: string;
  onRetry: () => void;
}

export function ErrorState({ resource, onRetry }: ErrorStateProps) {
  return (
    <div
      className="panel flex items-center justify-between gap-4 p-4"
      role="alert"
      style={{ borderColor: "var(--sev-severe)" }}
    >
      <div className="flex min-w-0 items-center gap-3">
        <AlertCircle className="shrink-0 text-sev-severe" aria-hidden="true" size={18} />
        <div>
          <div className="label mb-1 text-sev-severe">{resource} error</div>
          <p className="m-0 text-sm text-text-muted">The dashboard could not refresh this resource.</p>
        </div>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex h-9 items-center gap-2 rounded-panel border border-border bg-surface-2 px-3 text-sm text-text hover:border-text-faint"
      >
        <RefreshCcw aria-hidden="true" size={15} />
        Retry
      </button>
    </div>
  );
}
