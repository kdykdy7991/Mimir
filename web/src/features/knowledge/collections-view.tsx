"use client";

import Link from "next/link";
import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ArrowUpRight,
  Boxes,
  ChevronDown,
  Clock,
  FileStack,
  Plus,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { useApiResource } from "@/api/use-api-resource";
import { Button, ConfirmDialog, EmptyState, ErrorState, LoadingState } from "@/components";
import type { CollectionDetail } from "@/types";

const EMPTY_COLLECTIONS: CollectionDetail[] = [];

type StatusFilter = "all" | "populated" | "empty";
type SortKey = "updated_desc" | "updated_asc" | "name_asc" | "document_count_desc";

const STATUS_OPTIONS: { label: string; value: StatusFilter }[] = [
  { label: "全部状态", value: "all" },
  { label: "已有内容", value: "populated" },
  { label: "空知识库", value: "empty" },
];

const SORT_OPTIONS: { label: string; value: SortKey }[] = [
  { label: "最近更新", value: "updated_desc" },
  { label: "最早更新", value: "updated_asc" },
  { label: "名称 A–Z", value: "name_asc" },
  { label: "文档数量", value: "document_count_desc" },
];

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium" }).format(
    new Date(value),
  );
}

function formatCount(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function compareName(a: CollectionDetail, b: CollectionDetail) {
  return a.name.localeCompare(b.name, "zh-Hans-CN");
}

function sortCollections(
  items: CollectionDetail[],
  sort: SortKey,
): CollectionDetail[] {
  const copy = [...items];
  switch (sort) {
    case "updated_desc":
      copy.sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
      break;
    case "updated_asc":
      copy.sort((a, b) => Date.parse(a.updated_at) - Date.parse(b.updated_at));
      break;
    case "name_asc":
      copy.sort(compareName);
      break;
    case "document_count_desc":
      copy.sort(
        (a, b) => (b.document_count ?? 0) - (a.document_count ?? 0),
      );
      break;
  }
  return copy;
}

export function CollectionsView() {
  const load = useCallback(
    (signal: AbortSignal) =>
      apiClient.listCollections({ limit: 100 }, signal),
    [],
  );
  const { data, error, loading, retry } = useApiResource(load);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("updated_desc");
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<CollectionDetail>();
  const [mutationError, setMutationError] = useState<unknown>();
  const [additionalItems, setAdditionalItems] = useState<CollectionDetail[]>(
    [],
  );
  const [nextCursor, setNextCursor] = useState<string | null>();
  const [loadingMore, setLoadingMore] = useState(false);

  const items = useMemo(
    () => [...(data?.items ?? EMPTY_COLLECTIONS), ...additionalItems],
    [data, additionalItems],
  );
  const trimmed = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    const matched = items.filter((item) => {
      if (statusFilter === "populated" && !(item.document_count > 0))
        return false;
      if (statusFilter === "empty" && (item.document_count ?? 0) > 0)
        return false;
      if (trimmed) {
        const haystack = `${item.name}\n${item.description ?? ""}`.toLowerCase();
        if (!haystack.includes(trimmed)) return false;
      }
      return true;
    });
    return sortCollections(matched, sortKey);
  }, [items, statusFilter, sortKey, trimmed]);

  const apiError = error instanceof ApiError ? error : undefined;
  const resolvedCursor =
    nextCursor === undefined ? data?.page_info.next_cursor : nextCursor;
  const hasMore = Boolean(resolvedCursor);
  const searchActive = Boolean(trimmed) || statusFilter !== "all";

  async function remove() {
    if (!deleteTarget) return;
    const targetId = deleteTarget.id;
    try {
      await apiClient.deleteCollection(targetId);
      setDeleteTarget(undefined);
      setAdditionalItems((current) =>
        current.filter((item) => item.id !== targetId),
      );
      retry();
    } catch (reason) {
      setMutationError(reason);
      setDeleteTarget(undefined);
    }
  }

  async function loadMore() {
    const cursor = resolvedCursor;
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await apiClient.listCollections({ cursor, limit: 100 });
      setAdditionalItems((current) => [...current, ...page.items]);
      setNextCursor(page.page_info.next_cursor ?? null);
    } catch (reason) {
      setMutationError(reason);
    } finally {
      setLoadingMore(false);
    }
  }

  function clearSearch() {
    setQuery("");
    setStatusFilter("all");
  }

  return (
    <div className="app-container space-y-6">
      <header className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
        <div className="min-w-0">
          <h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">
            知识库
          </h1>
          <p className="mt-2 max-w-2xl text-muted-foreground">
            集中管理企业知识库，为 AI 提供高质量的知识来源。
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus aria-hidden="true" className="size-4" />
          新建知识库
        </Button>
      </header>

      <section className="glass-surface rounded-xl p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <label className="relative block min-w-0 flex-1">
            <span className="sr-only">搜索知识库</span>
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            />
            <input
              aria-label="搜索知识库"
              className="h-10 w-full rounded-md border border-border bg-surface pl-9 pr-3 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索知识库（支持按名称、描述搜索）"
              value={query}
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <SelectField
              label="按状态筛选"
              onChange={(value) => setStatusFilter(value as StatusFilter)}
              options={STATUS_OPTIONS}
              value={statusFilter}
            />
            <SelectField
              label="排序方式"
              onChange={(value) => setSortKey(value as SortKey)}
              options={SORT_OPTIONS}
              value={sortKey}
            />
          </div>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          共 {items.length} 个知识库
          {hasMore ? "（已加载部分，筛选仅覆盖当前已加载数据）" : ""}
          {searchActive && !hasMore ? " · 已应用筛选 / 排序" : ""}
        </p>
      </section>

      {mutationError ? (
        <ErrorState
          {...(mutationError instanceof ApiError
            ? {
                code: mutationError.code,
                ...(mutationError.requestId
                  ? { requestId: mutationError.requestId }
                  : {}),
              }
            : {})}
          {...(mutationError instanceof Error
            ? { description: mutationError.message }
            : {})}
          title="操作失败"
        />
      ) : null}

      {loading ? (
        <LoadingState label="正在加载知识库" rows={5} />
      ) : error ? (
        <ErrorState
          {...(apiError
            ? {
                code: apiError.code,
                ...(apiError.requestId
                  ? { requestId: apiError.requestId }
                  : {}),
              }
            : {})}
          {...(error instanceof Error ? { description: error.message } : {})}
          onRetry={retry}
          title="无法加载知识库"
        />
      ) : filtered.length === 0 ? (
        searchActive ? (
          <EmptyState
            action={
              <Button onClick={clearSearch} variant="secondary">
                <X aria-hidden="true" className="size-4" />
                清除搜索
              </Button>
            }
            description="尝试更换关键词，或重置筛选条件。"
            icon={Search}
            title="没有搜索结果"
          />
        ) : (
          <EmptyState
            action={
              <Button onClick={() => setCreateOpen(true)}>
                <Plus aria-hidden="true" className="size-4" />
                新建知识库
              </Button>
            }
            description="创建知识库后即可上传文档并搭建检索。"
            icon={Boxes}
            title="暂无知识库"
          />
        )
      ) : (
        <>
          <section
            aria-label="知识库列表"
            className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
          >
            {filtered.map((collection) => (
              <CollectionCard
                collection={collection}
                key={collection.id}
                onDelete={() => setDeleteTarget(collection)}
              />
            ))}
          </section>
          {hasMore ? (
            <div className="flex justify-center">
              <Button loading={loadingMore} onClick={loadMore} variant="secondary">
                加载更多
              </Button>
            </div>
          ) : null}
        </>
      )}

      <CreateCollectionDialog
        onCreated={() => {
          setCreateOpen(false);
          retry();
        }}
        onOpenChange={setCreateOpen}
        open={createOpen}
      />
      <ConfirmDialog
        cancelLabel="取消"
        confirmLabel="删除"
        description={
          deleteTarget
            ? `即将永久删除“${deleteTarget.name}”，其中的 ${formatCount(deleteTarget.document_count ?? 0)} 份文档与所有片段也会一并清除，且无法恢复。`
            : "删除后无法恢复，且其中的文档与片段也会被清除。"
        }
        destructive
        onConfirm={remove}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(undefined);
        }}
        open={Boolean(deleteTarget)}
        title="确认删除该知识库？"
      />
    </div>
  );
}

function SelectField({
  label,
  onChange,
  options,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  options: { label: string; value: string }[];
  value: string;
}) {
  return (
    <label className="relative block">
      <span className="sr-only">{label}</span>
      <select
        aria-label={label}
        className="h-10 appearance-none rounded-md border border-border bg-surface px-3 pr-9 text-sm outline-none transition-colors focus:border-primary"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
      />
    </label>
  );
}

function CollectionCard({
  collection,
  onDelete,
}: {
  collection: CollectionDetail;
  onDelete: () => void;
}) {
  return (
    <article className="glass-surface group relative flex min-h-64 cursor-pointer flex-col rounded-xl p-5 transition-[border-color,box-shadow,background-color] duration-200 hover:border-primary/45 hover:bg-primary/[0.025] hover:shadow-[0_14px_32px_-16px_color-mix(in_srgb,var(--color-primary)_45%,transparent)] focus-within:border-primary/55 focus-within:ring-2 focus-within:ring-primary/25 active:scale-[0.995]">
      <Link
        aria-label={`打开知识库 ${collection.name}`}
        className="absolute inset-0 rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/45"
        href={`/collections/${collection.id}`}
      />
      <div className="flex items-start justify-between gap-3">
        <span className="grid size-11 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary transition-transform duration-200 group-hover:scale-105 group-hover:ring-4 group-hover:ring-primary/10">
          <Boxes aria-hidden="true" className="size-5" />
        </span>
        <button
          aria-label={`删除知识库 ${collection.name}`}
          className="relative z-10 grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-danger/10 hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/40"
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            onDelete();
          }}
          title={`删除 ${collection.name}`}
          type="button"
        >
          <Trash2 aria-hidden="true" className="size-4" />
        </button>
      </div>
      <h2
        className="mt-5 line-clamp-1 text-lg font-semibold tracking-[-0.015em] transition-colors group-hover:text-primary"
        title={collection.name}
      >
        {collection.name}
      </h2>
      <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">
        {collection.description || "暂无描述"}
      </p>
      <div className="mt-auto flex items-center gap-5 pt-6 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <FileStack aria-hidden="true" className="size-3.5" />
          {formatCount(collection.document_count ?? 0)} 文档
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Sparkles aria-hidden="true" className="size-3.5" />
          {formatCount(collection.chunk_count ?? 0)} 片段
        </span>
      </div>
      <div className="mt-4 flex items-center justify-between border-t border-border pt-4 transition-colors group-hover:border-primary/20">
        <span className="inline-flex items-center gap-1.5 text-[0.6875rem] text-muted-foreground">
          <Clock aria-hidden="true" className="size-3" />
          更新于 {formatDate(collection.updated_at)}
        </span>
        <ArrowUpRight
          aria-hidden="true"
          className="size-4 -translate-x-1 translate-y-1 text-muted-foreground opacity-45 transition-all duration-200 group-hover:translate-x-0 group-hover:translate-y-0 group-hover:text-primary group-hover:opacity-100"
        />
      </div>
    </article>
  );
}

function CreateCollectionDialog({
  onCreated,
  onOpenChange,
  open,
}: {
  onCreated: () => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}) {
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
    setPending(true);
    setError(undefined);
    try {
      await apiClient.createCollection({
        name: String(form.get("name") ?? "").trim(),
        description: String(form.get("description") ?? "").trim() || null,
      });
      onCreated();
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
            <h2 className="text-lg font-semibold">新建知识库</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              名称必须唯一，创建后即可上传文档。
            </p>
          </div>
          <button
            aria-label="关闭"
            className="grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground"
            onClick={() => onOpenChange(false)}
            type="button"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        </div>
        <div className="space-y-4 p-6">
          <label className="block text-sm font-medium">
            名称
            <input
              autoFocus
              className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3 outline-none focus:border-primary"
              maxLength={128}
              name="name"
              required
            />
          </label>
          <label className="block text-sm font-medium">
            描述
            <textarea
              className="mt-2 min-h-24 w-full resize-y rounded-md border border-border bg-surface p-3 outline-none focus:border-primary"
              maxLength={1024}
              name="description"
            />
          </label>
          {error ? (
            <p className="text-xs text-danger">
              {error instanceof Error ? error.message : "创建失败"}
            </p>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-border bg-surface-muted/50 px-6 py-4">
          <Button onClick={() => onOpenChange(false)} type="button" variant="ghost">
            取消
          </Button>
          <Button loading={pending} type="submit">
            创建
          </Button>
        </div>
      </form>
    </dialog>
  );
}