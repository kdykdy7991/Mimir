"use client";

import { CircleAlert, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";

export function ErrorState({
  code,
  description = "请求没有成功完成，请稍后重试。",
  onRetry,
  requestId,
  title = "加载失败",
}: {
  code?: string;
  description?: string;
  onRetry?: () => void;
  requestId?: string;
  title?: string;
}) {
  return (
    <section
      className="rounded-lg border border-danger/20 bg-danger/5 p-5"
      role="alert"
    >
      <div className="flex gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-md bg-danger/10 text-danger">
          <CircleAlert aria-hidden="true" className="size-[1.125rem]" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {description}
          </p>
          {code || requestId ? (
            <p className="mt-3 break-all font-mono text-[0.6875rem] text-muted-foreground">
              {[code, requestId ? `request: ${requestId}` : null]
                .filter(Boolean)
                .join(" · ")}
            </p>
          ) : null}
          {onRetry ? (
            <Button className="mt-4" onClick={onRetry} size="sm" variant="secondary">
              <RotateCcw aria-hidden="true" className="size-3.5" />
              重试
            </Button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
