"use client";

import { TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

export function ConfirmDialog({
  cancelLabel = "取消",
  confirmLabel = "确认",
  description,
  destructive = false,
  onConfirm,
  onOpenChange,
  open,
  title,
}: {
  cancelLabel?: string;
  confirmLabel?: string;
  description: string;
  destructive?: boolean;
  onConfirm: () => Promise<void> | void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  title: string;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  async function handleConfirm() {
    setPending(true);
    try {
      await onConfirm();
      onOpenChange(false);
    } finally {
      setPending(false);
    }
  }

  return (
    <dialog
      aria-labelledby="confirm-dialog-title"
      className="m-auto w-[min(calc(100%-2rem),28rem)] rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-lg backdrop:bg-foreground/20 backdrop:backdrop-blur-sm"
      onCancel={(event) => {
        event.preventDefault();
        if (!pending) onOpenChange(false);
      }}
      onClick={(event) => {
        if (event.target === dialogRef.current && !pending) onOpenChange(false);
      }}
      ref={dialogRef}
    >
      <div className="p-6">
        <div className="flex items-start gap-3">
          <span
            className={`grid size-10 shrink-0 place-items-center rounded-lg ${
              destructive
                ? "bg-danger/10 text-danger"
                : "bg-warning/10 text-warning"
            }`}
          >
            <TriangleAlert aria-hidden="true" className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <h2
              className="text-base font-semibold tracking-[-0.015em]"
              id="confirm-dialog-title"
            >
              {title}
            </h2>
            <p className="text-pretty mt-2 text-xs leading-5 text-muted-foreground">
              {description}
            </p>
          </div>
          <button
            aria-label="关闭"
            className="grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground disabled:opacity-50"
            disabled={pending}
            onClick={() => onOpenChange(false)}
            type="button"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        </div>
      </div>
      <div className="flex justify-end gap-2 border-t border-border bg-surface-muted/50 px-6 py-4">
        <Button
          disabled={pending}
          onClick={() => onOpenChange(false)}
          variant="ghost"
        >
          {cancelLabel}
        </Button>
        <Button
          loading={pending}
          onClick={handleConfirm}
          variant={destructive ? "danger" : "primary"}
        >
          {confirmLabel}
        </Button>
      </div>
    </dialog>
  );
}
