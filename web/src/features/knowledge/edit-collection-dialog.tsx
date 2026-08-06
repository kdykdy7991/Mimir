"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { Button } from "@/components";
import type { CollectionDetail } from "@/types";

type Props = {
  collection: CollectionDetail;
  onOpenChange: (open: boolean) => void;
  onUpdated: () => void;
  open: boolean;
};

export function EditCollectionDialog({ collection, onOpenChange, onUpdated, open }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const description = String(form.get("description") ?? "").trim() || null;
    setPending(true);
    setError(undefined);
    try {
      await apiClient.updateCollection(collection.id, { description });
      onUpdated();
    } catch (reason) {
      setError(reason);
    } finally {
      setPending(false);
    }
  }

  return (
    <dialog
      className="m-auto w-[min(calc(100%-2rem),32rem)] rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-lg backdrop:bg-foreground/20"
      ref={ref}
    >
      <form onSubmit={submit}>
        <div className="flex items-start justify-between border-b border-border p-6">
          <div>
            <h2 className="text-lg font-semibold">编辑知识库描述</h2>
            <p className="mt-1 text-xs text-muted-foreground">{collection.name}</p>
          </div>
          <button aria-label="关闭" onClick={() => onOpenChange(false)} type="button">
            <X className="size-4" />
          </button>
        </div>
        <div className="space-y-4 p-6">
          <label className="block text-sm font-medium">
            描述
            <textarea
              autoFocus
              className="mt-2 min-h-32 w-full resize-y rounded-md border border-border bg-surface p-3 outline-none focus:border-primary"
              defaultValue={collection.description ?? ""}
              maxLength={1024}
              name="description"
              placeholder="说明这个知识库包含什么内容、适用于哪些场景……"
            />
          </label>
          <p className="text-xs text-muted-foreground">最多 1024 个字符；留空可清除描述。</p>
          {error ? <p className="text-xs text-danger">{error instanceof ApiError ? `${error.code}：${error.message}` : error instanceof Error ? error.message : "保存失败"}</p> : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-border bg-surface-muted/50 px-6 py-4">
          <Button onClick={() => onOpenChange(false)} type="button" variant="ghost">取消</Button>
          <Button loading={pending} type="submit">保存修改</Button>
        </div>
      </form>
    </dialog>
  );
}
