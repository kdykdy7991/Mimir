"use client";

import {
  type InputHTMLAttributes,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  FileText,
  ImageIcon,
  MoreHorizontal,
  Sparkles,
  Tags,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";

import { apiClient } from "@/api";
import { Button, StatusBadge } from "@/components";
import type { DocumentSummary, DocumentTag } from "@/types";
import { tagPillClass } from "./tag-color";
import { documentExtension, parsingMethod } from "./document-presentation";

export type DocumentRow = DocumentSummary & { collectionName?: string };

const formatSize = (bytes: number) =>
  bytes < 1048576
    ? `${(bytes / 1024).toFixed(1)} KB`
    : `${(bytes / 1048576).toFixed(1)} MB`;

const TAG_LIMIT = 2;

export function DocumentTable({
  documents,
  onDelete,
  selectedIds,
  onSelectionChange,
  onChanged,
}: {
  documents: DocumentRow[];
  onDelete?: (document: DocumentRow) => void;
  selectedIds?: Set<string>;
  onSelectionChange?: (ids: Set<string>) => void;
  onChanged?: () => void;
}) {
  const [tagTarget, setTagTarget] = useState<DocumentRow>();
  const selectable = Boolean(selectedIds && onSelectionChange);
  const allSelected =
    selectable &&
    documents.length > 0 &&
    documents.every((item) => selectedIds?.has(item.id));
  const someSelected =
    selectable && documents.some((item) => selectedIds?.has(item.id));
  return (
    <>
      <div className="overflow-hidden rounded-lg border border-border bg-surface">
        <div className="hidden grid-cols-[2rem_minmax(0,1fr)_5rem_7rem_8rem_7rem_10rem_4.5rem] items-center gap-4 border-b border-border bg-surface-muted/65 px-5 py-3 text-xs font-semibold text-muted-foreground lg:grid">
          {selectable ? (
            <SelectionCheckbox
              aria-label="全选当前页"
              checked={allSelected}
              indeterminate={someSelected && !allSelected}
              onChange={(event) =>
                onSelectionChange?.(
                  event.target.checked
                    ? new Set(documents.map((item) => item.id))
                    : new Set(),
                )
              }
            />
          ) : (
            <span />
          )}
          <span>文档</span>
          <span>格式</span>
          <span>解析方式</span>
          <span>状态</span>
          <span>片段 / 图片</span>
          <span>更新时间</span>
          <span className="sr-only">操作</span>
        </div>
        <div className="divide-y divide-border">
          {documents.map((document) => (
            <DocumentRowView
              document={document}
              key={`${document.collection_id}:${document.id}`}
              {...(onDelete ? { onDelete } : {})}
              onEditTags={() => setTagTarget(document)}
              selectable={selectable}
              selected={Boolean(selectedIds?.has(document.id))}
              {...(onSelectionChange ? { onSelectionChange } : {})}
              {...(selectedIds ? { selectionSet: selectedIds } : {})}
            />
          ))}
        </div>
      </div>
      <DocumentTagDialog
        document={tagTarget}
        onClose={() => setTagTarget(undefined)}
        onSaved={() => {
          setTagTarget(undefined);
          if (onChanged) onChanged();
          else window.location.reload();
        }}
      />
    </>
  );
}

function DocumentRowView({
  document,
  onDelete,
  onEditTags,
  selectable,
  selected,
  onSelectionChange,
  selectionSet,
}: {
  document: DocumentRow;
  onDelete?: (document: DocumentRow) => void;
  onEditTags: () => void;
  selectable: boolean;
  selected: boolean;
  onSelectionChange?: (ids: Set<string>) => void;
  selectionSet?: Set<string>;
}) {
  const tags = document.tags ?? [];
  const visibleTags = tags.slice(0, TAG_LIMIT);
  const overflow = tags.length - TAG_LIMIT;
  const updated = new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(document.updated_at));
  return (
    <article
      className="grid min-w-0 gap-4 p-4 lg:grid-cols-[2rem_minmax(0,1fr)_5rem_7rem_8rem_7rem_10rem_4.5rem] lg:items-center lg:px-5"
      data-selected={selected ? "true" : undefined}
    >
      {selectable ? (
        <input
          aria-label={`选择 ${document.filename}`}
          checked={selected}
          className="self-start lg:self-center"
          onChange={(event) => {
            if (!onSelectionChange || !selectionSet) return;
            const next = new Set(selectionSet);
            if (event.target.checked) next.add(document.id);
            else next.delete(document.id);
            onSelectionChange(next);
          }}
          type="checkbox"
        />
      ) : (
        <span className="hidden lg:block" />
      )}
      <div className="flex min-w-0 items-center gap-3 overflow-hidden">
        <span className="grid size-10 shrink-0 place-items-center rounded-md bg-primary/10 text-primary">
          <FileText className="size-5" />
        </span>
        <div className="min-w-0 flex-1 overflow-hidden">
          <Link
            className="block max-w-full truncate font-medium hover:text-primary"
            href={`/documents/${document.id}`}
            title={document.filename}
          >
            {document.filename}
          </Link>
          {document.collectionName ? (
            <p
              className="mt-0.5 truncate text-xs text-muted-foreground"
              title={document.collectionName}
            >
              {document.collectionName}
            </p>
          ) : null}
          <div className="mt-1 hidden flex-wrap gap-1 lg:hidden">
            <StatusBadge status={document.status} />
            <span className="text-xs text-muted-foreground">{documentExtension(document.filename)}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {visibleTags.map((tag) => (
              <span
                className={`max-w-24 truncate rounded-full px-2 py-0.5 text-[0.6875rem] ${tagPillClass(tag.color)}`}
                key={tag.id}
                title={tag.name}
              >
                {tag.name}
              </span>
            ))}
            {overflow > 0 ? (
              <span
                className="text-xs text-muted-foreground"
                title={tags
                  .slice(TAG_LIMIT)
                  .map((tag) => tag.name)
                  .join("、")}
              >
                +{overflow}
              </span>
            ) : null}
          </div>
        </div>
      </div>
      <span className="text-xs font-semibold text-muted-foreground">
        {documentExtension(document.filename)}
      </span>
      <span
        className={`inline-flex items-center gap-1.5 text-xs ${parsingMethod(document.filename) === "视觉解析" ? "text-accent" : "text-muted-foreground"}`}
      >
        {parsingMethod(document.filename) === "视觉解析" ? (
          <Sparkles className="size-3.5" />
        ) : null}
        {parsingMethod(document.filename)}
      </span>
      <StatusBadge status={document.status} />
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span>{document.chunk_count ?? 0} 段</span>
        <span className="inline-flex items-center gap-1">
          <ImageIcon className="size-3" />
          {document.image_count ?? 0}
        </span>
      </div>
      <span
        className="text-xs text-muted-foreground"
        title={new Date(document.updated_at).toLocaleString()}
      >
        {updated}
      </span>
      <div className="flex items-center justify-end gap-1">
        <button
          aria-label={`编辑 ${document.filename} 的标签`}
          className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-primary/10 hover:text-primary"
          onClick={onEditTags}
          title="编辑标签"
          type="button"
        >
          <Tags className="size-4" />
        </button>
        {onDelete ? (
          <button
            aria-label={`删除 ${document.filename}`}
            className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-danger/10 hover:text-danger"
            onClick={() => onDelete(document)}
            title={`删除 · ${formatSize(document.size_bytes)}`}
            type="button"
          >
            <Trash2 className="size-4" />
          </button>
        ) : (
          <MoreHorizontal className="size-4 text-muted-foreground" />
        )}
      </div>
    </article>
  );
}

function SelectionCheckbox({
  indeterminate,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { indeterminate: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return <input {...props} ref={ref} type="checkbox" />;
}

function DocumentTagDialog({
  document,
  onClose,
  onSaved,
}: {
  document: DocumentRow | undefined;
  onClose: () => void;
  onSaved: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [tags, setTags] = useState<DocumentTag[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  // Snapshot the in-flight selection so a failed save can restore it without
  // losing the user's work — per the detail-page brief §11.
  const initialRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!document) {
      ref.current?.close();
      return;
    }
    const initial = new Set(document.tags?.map((tag) => tag.id) ?? []);
    initialRef.current = initial;
    ref.current?.showModal();
    Promise.resolve().then(() => {
      setSelected(new Set(initial));
      setQuery("");
      setError("");
      setLoading(true);
    });
    const controller = new AbortController();
    void apiClient
      .listDocumentTags(document.collection_id, controller.signal)
      .then(setTags)
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "标签加载失败"),
      )
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [document]);
  async function save() {
    if (!document) return;
    setSaving(true);
    setError("");
    try {
      await apiClient.replaceDocumentTags(document.id, [...selected]);
      onSaved();
    } catch (reason) {
      // Restore the snapshot so the user can retry without losing their
      // intended changes (selection survives the error).
      setSelected(new Set(initialRef.current));
      setError(reason instanceof Error ? reason.message : "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }
  const visible = tags.filter((tag) =>
    tag.name.toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <dialog
      aria-labelledby="document-tag-dialog-title"
      className="m-auto w-[min(calc(100%-2rem),32rem)] rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-xl backdrop:bg-foreground/20"
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
      ref={ref}
    >
      <div className="flex items-center justify-between border-b border-border p-5">
        <div className="min-w-0">
          <h2
            className="font-semibold"
            id="document-tag-dialog-title"
          >
            编辑文档标签
          </h2>
          <p
            className="mt-1 truncate text-xs text-muted-foreground"
            title={document?.filename}
          >
            {document?.filename}
          </p>
        </div>
        <button
          aria-label="关闭标签编辑"
          className="grid size-8 place-items-center rounded-md hover:bg-surface-muted"
          onClick={onClose}
          type="button"
        >
          <X className="size-4" />
        </button>
      </div>
      <div className="p-5">
        <input
          aria-label="搜索标签"
          className="h-9 w-full rounded-md border border-border bg-surface px-3 text-sm"
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索标签…"
          value={query}
        />
        <div className="mt-4 max-h-64 space-y-2 overflow-y-auto">
          {loading ? (
            <p className="text-sm text-muted-foreground">正在加载标签…</p>
          ) : visible.length ? (
            visible.map((tag) => (
              <label
                className="flex cursor-pointer items-center gap-3 rounded-md border border-border px-3 py-2 text-sm"
                key={tag.id}
              >
                <input
                  checked={selected.has(tag.id)}
                  onChange={(event) => {
                    const next = new Set(selected);
                    if (event.target.checked) next.add(tag.id);
                    else next.delete(tag.id);
                    setSelected(next);
                  }}
                  type="checkbox"
                />
                <span className="min-w-0 flex-1 truncate" title={tag.name}>
                  {tag.name}
                </span>
              </label>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">
              没有可选标签，请先在知识库中创建。
            </p>
          )}
        </div>
        {error ? <p className="mt-3 text-sm text-danger">{error}</p> : null}
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={onClose} variant="secondary">
            取消
          </Button>
          <Button loading={saving} onClick={() => void save()}>
            保存标签
          </Button>
        </div>
      </div>
    </dialog>
  );
}