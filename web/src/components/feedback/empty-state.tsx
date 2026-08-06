import { Inbox, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({
  action,
  description,
  icon: Icon = Inbox,
  title,
}: {
  action?: ReactNode;
  description: string;
  icon?: LucideIcon;
  title: string;
}) {
  return (
    <section className="grid min-h-64 place-items-center rounded-lg border border-dashed border-border-strong bg-surface/55 px-6 py-12 text-center">
      <div className="max-w-sm">
        <span className="mx-auto grid size-11 place-items-center rounded-lg border border-border bg-surface-muted text-muted-foreground shadow-sm">
          <Icon aria-hidden="true" className="size-5" />
        </span>
        <h2 className="mt-4 text-sm font-semibold tracking-[-0.01em]">
          {title}
        </h2>
        <p className="text-pretty mt-2 text-xs leading-5 text-muted-foreground">
          {description}
        </p>
        {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
      </div>
    </section>
  );
}
