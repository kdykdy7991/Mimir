import { cn } from "@/lib";

export function Skeleton({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "block animate-pulse rounded-md bg-[linear-gradient(90deg,var(--surface-muted),color-mix(in_srgb,var(--surface-muted)_55%,var(--surface-raised)),var(--surface-muted))] bg-[length:200%_100%]",
        className,
      )}
    />
  );
}
