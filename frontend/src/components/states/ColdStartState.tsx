import { Database } from "lucide-react";

export function ColdStartState() {
  return (
    <div className="panel flex items-start gap-4 p-5">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-panel border border-border bg-surface-2 text-text-muted">
        <Database aria-hidden="true" size={18} strokeWidth={1.8} />
      </div>
      <div>
        <div className="label mb-2">Cold start</div>
        <p className="m-0 max-w-3xl text-sm leading-6 text-text-muted">
          The poller is gathering its first readings. Statistical detectors activate after about
          12 readings, and same-hour baselines need several days of history.
        </p>
      </div>
    </div>
  );
}
