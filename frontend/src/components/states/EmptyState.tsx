import { SearchX } from "lucide-react";

interface EmptyStateProps {
  title: string;
  detail: string;
}

export function EmptyState({ title, detail }: EmptyStateProps) {
  return (
    <div className="panel flex min-h-40 items-center gap-4 p-5">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-panel border border-border bg-surface-2 text-text-muted">
        <SearchX aria-hidden="true" size={18} strokeWidth={1.8} />
      </div>
      <div>
        <div className="label mb-2">{title}</div>
        <p className="m-0 text-sm leading-6 text-text-muted">{detail}</p>
      </div>
    </div>
  );
}
