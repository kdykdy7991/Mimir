import { Skeleton } from "./skeleton";

export function LoadingState({
  label = "正在加载",
  rows = 3,
}: {
  label?: string;
  rows?: number;
}) {
  return (
    <div
      aria-busy="true"
      aria-label={label}
      className="rounded-lg border border-border bg-surface p-5"
      role="status"
    >
      <span className="sr-only">{label}</span>
      <div className="flex items-center gap-3">
        <Skeleton className="size-10 shrink-0 rounded-lg" />
        <div className="min-w-0 flex-1 space-y-2">
          <Skeleton className="h-3.5 w-2/5" />
          <Skeleton className="h-3 w-3/5" />
        </div>
      </div>
      <div className="mt-5 space-y-3">
        {Array.from({ length: Math.max(1, rows) }, (_, index) => (
          <Skeleton
            className={index === rows - 1 ? "h-3 w-4/5" : "h-3 w-full"}
            key={index}
          />
        ))}
      </div>
    </div>
  );
}
