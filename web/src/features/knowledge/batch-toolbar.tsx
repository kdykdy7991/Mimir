"use client";

import Link from "next/link";
import { useState } from "react";
import { AlertTriangle, Tags as TagsIcon, Trash2, X } from "lucide-react";

import { Button, ConfirmDialog, StatusBadge } from "@/components";
import type {
  BatchDocumentResponse,
  DocumentFolder,
  DocumentTag,
} from "@/types";

// Mirrors src/web_api/schemas/batch.py — kept in lock-step with the backend
// caps so the UI disables actions that the API would reject anyway.
export const BATCH_LIMIT_GENERAL = 100;
export const BATCH_LIMIT_REPROCESS = 20;

export type BatchTagAction = "add" | "remove" | "replace";

export function BatchToolbar({
  folders,
  pending,
  selectedCount,
  tags,
  tagAction,
  onApplyTag,
  onClearSelection,
  onDelete,
  onMove,
  onReprocess,
  onTagActionChange,
}: {
  folders: DocumentFolder[];
  pending: boolean;
  selectedCount: number;
  tags: DocumentTag[];
  tagAction: BatchTagAction;
  onApplyTag: (tagId: string) => void;
  onClearSelection: () => void;
  onDelete: () => void;
  onMove: (folderId: string | null) => void;
  onReprocess: () => void;
  onTagActionChange: (action: BatchTagAction) => void;
}) {
  const [confirmAction, setConfirmAction] = useState<"delete" | "reprocess" | null>(
    null,
  );
  const reprocessOverLimit = selectedCount > BATCH_LIMIT_REPROCESS;
  const generalOverLimit = selectedCount > BATCH_LIMIT_GENERAL;
  return (
    <>
      <div
        aria-live="polite"
        className="sticky top-4 z-10 mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-primary/25 bg-primary/10 p-3 shadow-sm backdrop-blur"
      >
        <strong className="mr-1 text-sm">
          已选 {selectedCount} 项
          {generalOverLimit ? (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-warning">
              <AlertTriangle aria-hidden="true" className="size-3" />
              超过 {BATCH_LIMIT_GENERAL} 项上限
            </span>
          ) : null}
        </strong>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-muted-foreground">
            <span className="sr-only">批量标签操作</span>
            <select
              aria-label="批量标签操作"
              className="h-9 rounded-md border border-border bg-surface px-2 text-sm"
              disabled={pending}
              onChange={(event) =>
                onTagActionChange(event.target.value as BatchTagAction)
              }
              value={tagAction}
            >
              <option value="add">添加标签</option>
              <option value="remove">移除标签</option>
              <option value="replace">替换为标签</option>
            </select>
          </label>
          <label className="text-xs text-muted-foreground">
            <span className="sr-only">选择标签</span>
            <select
              aria-label="选择批量标签"
              className="h-9 rounded-md border border-border bg-surface px-2 text-sm"
              defaultValue=""
              disabled={pending || !tags.length}
              onChange={(event) => {
                if (event.target.value) onApplyTag(event.target.value);
                event.target.value = "";
              }}
            >
              <option value="">
                {tags.length ? "选择标签…" : "暂无标签"}
              </option>
              {tags.map((tag) => (
                <option key={tag.id} value={tag.id}>
                  {tag.name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-muted-foreground">
            <span className="sr-only">移动到文件夹</span>
            <select
              aria-label="批量移动文件夹"
              className="h-9 rounded-md border border-border bg-surface px-2 text-sm"
              defaultValue=""
              disabled={pending}
              onChange={(event) => {
                const value = event.target.value;
                onMove(value === "" ? null : value);
                event.target.value = "";
              }}
            >
              <option value="">移动到根目录</option>
              {folders.map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {folder.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button
            disabled={pending || reprocessOverLimit}
            onClick={() => setConfirmAction("reprocess")}
            title={
              reprocessOverLimit
                ? `批量重新解析每次最多 ${BATCH_LIMIT_REPROCESS} 份文档`
                : undefined
            }
            variant="secondary"
          >
            重新解析
          </Button>
          <Button
            disabled={pending}
            onClick={() => setConfirmAction("delete")}
            variant="danger"
          >
            <Trash2 aria-hidden="true" className="size-4" />
            批量删除
          </Button>
          <Button
            aria-label="清除选择"
            onClick={onClearSelection}
            size="icon"
            type="button"
            variant="ghost"
          >
            <X aria-hidden="true" className="size-4" />
          </Button>
        </div>
      </div>
      <ConfirmDialog
        cancelLabel="取消"
        confirmLabel={
          confirmAction === "delete" ? "删除文档" : "重新解析"
        }
        description={
          confirmAction === "delete"
            ? `即将永久删除 ${selectedCount} 份文档，同时清理其向量、稀疏索引、图片与完整性记录，无法恢复。`
            : `即将对 ${selectedCount} 份文档触发重新解析流程，会在后台异步执行。`
        }
        destructive={confirmAction === "delete"}
        onConfirm={() => {
          if (confirmAction === "delete") onDelete();
          else onReprocess();
        }}
        onOpenChange={(open) => {
          if (!open) setConfirmAction(null);
        }}
        open={Boolean(confirmAction)}
        title={
          confirmAction === "delete"
            ? `确认删除 ${selectedCount} 份文档？`
            : `确认重新解析 ${selectedCount} 份文档？`
        }
      />
    </>
  );
}

export function BatchResultPanel({
  result,
  onDismiss,
}: {
  result: BatchDocumentResponse;
  onDismiss?: () => void;
}) {
  const failed = result.items.filter((item) => item.status === "error");
  const succeeded = result.items.length - failed.length;
  const status =
    failed.length === 0
      ? "succeeded"
      : failed.length === result.items.length
        ? "failed"
        : "warning";
  return (
    <section
      aria-live="polite"
      className="mt-3 rounded-lg border border-border bg-surface-muted/40 p-3 text-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge
            label={
              status === "succeeded"
                ? "全部成功"
                : status === "failed"
                  ? "全部失败"
                  : "部分失败"
            }
            status={status}
          />
          <p className="font-medium">
            批量操作完成 · 共 {result.items.length} 项，成功 {succeeded} 项
            {failed.length ? `，失败 ${failed.length} 项` : ""}
          </p>
        </div>
        {onDismiss ? (
          <button
            aria-label="关闭批量结果"
            className="rounded p-1 text-muted-foreground hover:bg-surface-muted"
            onClick={onDismiss}
            type="button"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        ) : null}
      </div>
      {failed.length ? (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-semibold text-danger">
            查看失败明细（{failed.length}）
          </summary>
          <ul className="mt-2 space-y-2 text-xs text-muted-foreground">
            {failed.map((item) => (
              <li
                className="rounded border border-danger/20 bg-danger/5 p-2"
                key={item.document_id}
              >
                <div className="flex items-center justify-between gap-2">
                  <span
                    className="break-all font-mono text-foreground"
                    title={item.document_id}
                  >
                    {item.document_id}
                  </span>
                  {item.task_id ? (
                    <Link
                      className="inline-flex items-center gap-1 font-semibold text-primary"
                      href={`/traces?type=ingestion&id=${item.task_id}`}
                    >
                      <TagsIcon aria-hidden="true" className="size-3" />
                      查看 Trace
                    </Link>
                  ) : null}
                </div>
                <p className="mt-1">
                  {item.error?.message ?? "操作失败"}
                  {item.error?.code ? (
                    <span className="ml-1 rounded bg-surface-muted px-1.5 py-0.5 font-mono text-[0.6875rem] text-foreground">
                      {item.error.code}
                    </span>
                  ) : null}
                </p>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}