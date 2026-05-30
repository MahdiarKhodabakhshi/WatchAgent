interface LoadingStateProps {
  label?: string;
  rows?: number;
}

export function LoadingState({ label = "Loading", rows = 3 }: LoadingStateProps) {
  return (
    <div className="panel p-4" role="status" aria-label={label}>
      <div className="label mb-4">{label}</div>
      <div className="space-y-3">
        {Array.from({ length: rows }).map((_, index) => (
          <div key={index} className="grid grid-cols-[1fr_5rem] gap-3">
            <div className="skeleton h-4 rounded-sm" />
            <div className="skeleton h-4 rounded-sm" />
          </div>
        ))}
      </div>
    </div>
  );
}
