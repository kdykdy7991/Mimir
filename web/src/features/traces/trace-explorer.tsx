"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  Braces,
  Clock3,
  DatabaseZap,
  FileInput,
  GitMerge,
  Layers3,
  Network,
  Search,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import {
  Button,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "@/components";
import type { ActionableTraceResponse, CollectionDetail } from "@/types";
import {
  isSkippedTrace,
  normalizeTraceStatus,
  shouldShowRawStageName,
  traceStageDescription,
  traceStageLabel,
} from "./trace-presentation";

type TraceType = "query" | "ingestion";
const initialSearch = (key: string) =>
  typeof window === "undefined"
    ? ""
    : (new URLSearchParams(window.location.search).get(key) ?? "");
const visuals: Record<string, { icon: LucideIcon; color: string }> = {
  dense: { icon: Network, color: "bg-primary" },
  dense_retrieval: { icon: Network, color: "bg-primary" },
  sparse: { icon: Braces, color: "bg-accent" },
  sparse_retrieval: { icon: Braces, color: "bg-accent" },
  fusion: { icon: GitMerge, color: "bg-info" },
  rerank: { icon: Sparkles, color: "bg-success" },
  load: { icon: FileInput, color: "bg-primary" },
  split: { icon: Layers3, color: "bg-accent" },
  transform: { icon: Braces, color: "bg-info" },
  embed: { icon: DatabaseZap, color: "bg-warning" },
  upsert: { icon: DatabaseZap, color: "bg-success" },
  query_processing: { icon: Search, color: "bg-primary" },
  trim: { icon: Layers3, color: "bg-success" },
};

export function TraceExplorer({
  initialId = "",
  initialType = "query",
}: {
  initialId?: string;
  initialType?: TraceType;
}) {
  const [id, setId] = useState(initialId);
  const [type, setType] = useState<TraceType>(initialType);
  const [trace, setTrace] = useState<ActionableTraceResponse>();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(Boolean(initialId));

  useEffect(() => {
    if (!initialId) return;
    let cancelled = false;
    const request =
      initialType === "query"
        ? apiClient.getQueryTrace(initialId)
        : apiClient.getIngestionTrace(initialId);
    request
      .then((value) => {
        if (!cancelled) setTrace(value);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason);
      })
      .finally(() => {
        if (!cancelled) setPending(false);
      });
    return () => {
      cancelled = true;
    };
  }, [initialId, initialType]);

  useEffect(() => {
    if (
      !trace ||
      type !== "ingestion" ||
      normalizeTraceStatus(trace.status) !== "running"
    )
      return;
    const controller = new AbortController();
    const timer = setTimeout(
      () => {
        void apiClient
          .getIngestionTrace(trace.id, controller.signal)
          .then(setTrace)
          .catch(() => undefined);
      },
      document.hidden ? 10_000 : 2_000,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [trace, type]);

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const value = id.trim();
    if (!value) return;
    setPending(true);
    setError(undefined);
    setTrace(undefined);
    try {
      setTrace(
        type === "query"
          ? await apiClient.getQueryTrace(value)
          : await apiClient.getIngestionTrace(value),
      );
    } catch (reason) {
      setError(reason);
    } finally {
      setPending(false);
    }
  }

  const apiError = error instanceof ApiError ? error : undefined;
  return (
    <div className="space-y-6">
      <TraceHistory
        onSelect={(item) => {
          setId(item.id);
          setType(item.trace_type);
          setTrace(item);
          setError(undefined);
        }}
      />
      <form className="glass-surface rounded-xl p-5" onSubmit={submit}>
        <div className="grid gap-4 lg:grid-cols-[12rem_minmax(0,1fr)_auto] lg:items-end">
          <label className="text-xs font-semibold text-muted-foreground">
            Trace 类型
            <select
              className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3 text-sm"
              onChange={(event) => setType(event.target.value as TraceType)}
              value={type}
            >
              <option value="query">Query</option>
              <option value="ingestion">Ingestion</option>
            </select>
          </label>
          <label className="text-xs font-semibold text-muted-foreground">
            Trace ID
            <input
              className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3 font-mono text-sm outline-none focus:border-primary"
              onChange={(event) => setId(event.target.value)}
              placeholder={
                type === "query" ? "输入 query_id" : "输入上传返回的 task_id"
              }
              value={id}
            />
          </label>
          <button
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground disabled:opacity-45"
            disabled={pending || !id.trim()}
            type="submit"
          >
            <Search className="size-4" />
            {pending ? "查询中…" : "读取 Trace"}
          </button>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          可从上方历史记录选择，也可直接输入 Query ID 或 Task ID。
        </p>
      </form>

      {pending ? (
        <LoadingState label="正在读取 Trace" rows={7} />
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
          description={
            apiError?.code === "QUERY_NOT_FOUND" ||
            apiError?.code === "INGESTION_NOT_FOUND"
              ? "没有找到对应 Trace，请确认类型与 ID 是否匹配。"
              : error instanceof Error
                ? error.message
                : "Trace 读取失败"
          }
          onRetry={() => void submit()}
          title="无法读取 Trace"
        />
      ) : trace ? (
        <TraceDetails
          onChanged={(next) => {
            setTrace(next);
            setId(next.id);
          }}
          trace={trace}
        />
      ) : (
        <EmptyState
          description="从 Playground 查询结果进入时会自动携带 Query Trace ID；也可以手动输入 Query ID 或 Task ID。"
          icon={Clock3}
          title="输入 ID 查看执行链路"
        />
      )}
    </div>
  );
}

function TraceHistory({
  onSelect,
}: {
  onSelect: (trace: ActionableTraceResponse) => void;
}) {
  const [type, setType] = useState<"" | TraceType>(() => {
    const value = initialSearch("type");
    return value === "query" || value === "ingestion" ? value : "";
  });
  const [status, setStatus] = useState(() => initialSearch("status"));
  const [query, setQuery] = useState(() => initialSearch("q"));
  const [collectionId, setCollectionId] = useState(() =>
    initialSearch("collection_id"),
  );
  const [startedFrom, setStartedFrom] = useState(() => initialSearch("from"));
  const [startedTo, setStartedTo] = useState(() => initialSearch("to"));
  const [cursor, setCursor] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [items, setItems] = useState<ActionableTraceResponse[]>([]);
  const [collections, setCollections] = useState<CollectionDetail[]>([]);
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    void apiClient
      .listCollections({ limit: 100 }, controller.signal)
      .then((value) => setCollections(value.items))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void apiClient
      .listTraces(
        {
          ...(type ? { type } : {}),
          ...(status ? { status } : {}),
          ...(query ? { q: query } : {}),
          ...(collectionId ? { collection_id: collectionId } : {}),
          ...(startedFrom
            ? { from: new Date(`${startedFrom}T00:00:00`).toISOString() }
            : {}),
          ...(startedTo
            ? { to: new Date(`${startedTo}T23:59:59`).toISOString() }
            : {}),
          ...(cursor ? { cursor } : {}),
          limit: 20,
        },
        controller.signal,
      )
      .then((value) => {
        setItems((current) =>
          cursor ? [...current, ...value.items] : value.items,
        );
        setNextCursor(value.page_info.next_cursor ?? null);
        setError(undefined);
      })
      .catch(setError)
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [collectionId, cursor, query, startedFrom, startedTo, status, type]);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries({
      type,
      status,
      q: query,
      collection_id: collectionId,
      from: startedFrom,
      to: startedTo,
    })) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${params.size ? `?${params}` : ""}`,
    );
  }, [collectionId, query, startedFrom, startedTo, status, type]);
  function reset() {
    setCursor(null);
    setNextCursor(null);
    setItems([]);
  }
  return (
    <section className="glass-surface rounded-xl p-5">
      <div className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <h2 className="font-semibold">Trace 历史</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            筛选并打开最近的查询和摄取链路。
          </p>
        </div>
        <select
          aria-label="筛选 Trace 类型"
          className="h-9 rounded-md border border-border bg-surface px-2 text-sm"
          onChange={(event) => {
            reset();
            setType(event.target.value as "" | TraceType);
          }}
          value={type}
        >
          <option value="">全部类型</option>
          <option value="query">Query</option>
          <option value="ingestion">Ingestion</option>
        </select>
        <select
          aria-label="筛选 Trace 状态"
          className="h-9 rounded-md border border-border bg-surface px-2 text-sm"
          onChange={(event) => {
            reset();
            setStatus(event.target.value);
          }}
          value={status}
        >
          <option value="">全部状态</option>
          <option value="running">处理中</option>
          <option value="failed">失败优先</option>
          <option value="success">已完成</option>
          <option value="canceled">已取消</option>
        </select>
        <input
          aria-label="搜索 Trace"
          className="h-9 rounded-md border border-border bg-surface px-3 text-sm"
          onChange={(event) => {
            reset();
            setQuery(event.target.value);
          }}
          placeholder="文档名 / Request ID"
          value={query}
        />
        <input
          aria-label="筛选知识库 ID"
          className="h-9 rounded-md border border-border bg-surface px-3 text-sm"
          onChange={(event) => {
            reset();
            setCollectionId(event.target.value);
          }}
          placeholder="知识库 ID"
          list="trace-collections"
          value={collectionId}
        />
        <datalist id="trace-collections">
          {collections.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </datalist>
        <label className="text-xs text-muted-foreground">
          开始日期
          <input
            className="mt-1 block h-9 rounded-md border border-border bg-surface px-2 text-sm"
            onChange={(event) => {
              reset();
              setStartedFrom(event.target.value);
            }}
            type="date"
            value={startedFrom}
          />
        </label>
        <label className="text-xs text-muted-foreground">
          结束日期
          <input
            className="mt-1 block h-9 rounded-md border border-border bg-surface px-2 text-sm"
            onChange={(event) => {
              reset();
              setStartedTo(event.target.value);
            }}
            type="date"
            value={startedTo}
          />
        </label>
      </div>
      {loading && !items.length ? (
        <div className="mt-4">
          <LoadingState label="加载 Trace 历史" rows={3} />
        </div>
      ) : error ? (
        <div className="mt-4">
          <ErrorState title="无法加载 Trace 历史" />
        </div>
      ) : (
        <>
          <div className="mt-4 divide-y divide-border rounded-lg border border-border">
            {items.length ? (
              items.map((item) => (
                <button
                  className="flex w-full items-center justify-between gap-3 p-3 text-left hover:bg-surface-muted"
                  key={item.id}
                  onClick={() => onSelect(item)}
                  type="button"
                >
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">
                      {item.trace_type === "query" ? "查询" : "文档处理"}
                    </span>
                    <span className="block truncate font-mono text-xs text-muted-foreground">
                      {item.id}
                    </span>
                  </span>
                  <StatusBadge status={normalizeTraceStatus(item.status)} />
                </button>
              ))
            ) : (
              <p className="p-4 text-sm text-muted-foreground">
                没有符合条件的 Trace。
              </p>
            )}
          </div>
          {nextCursor ? (
            <div className="mt-3 text-center">
              <Button onClick={() => setCursor(nextCursor)} variant="secondary">
                加载更多
              </Button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function TraceDetails({
  trace,
  onChanged,
}: {
  trace: ActionableTraceResponse;
  onChanged: (trace: ActionableTraceResponse) => void;
}) {
  const stages = trace.stages ?? [];
  const skipped = isSkippedTrace(trace);
  const status = trace.error
    ? "failed"
    : skipped
      ? "skipped"
      : normalizeTraceStatus(trace.status ?? "success");
  const [actionPending, setActionPending] = useState<"retry" | "cancel" | null>(
    null,
  );
  async function retry() {
    if (
      actionPending ||
      !window.confirm("将创建新的处理任务并保留原 Trace，确认重试？")
    )
      return;
    setActionPending("retry");
    try {
      const task = await apiClient.retryTask(trace.id);
      onChanged(await apiClient.getIngestionTrace(task.id));
    } finally {
      setActionPending(null);
    }
  }
  async function cancel() {
    if (
      actionPending ||
      !window.confirm("取消不会回滚已经完成的阶段，确认继续？")
    )
      return;
    setActionPending("cancel");
    try {
      await apiClient.cancelTask(trace.id);
      onChanged(await apiClient.getIngestionTrace(trace.id));
    } finally {
      setActionPending(null);
    }
  }
  return (
    <section className="glass-surface rounded-xl p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">
              {trace.trace_type === "query" ? "查询链路" : "文档处理链路"}
            </h2>
            <StatusBadge status={status} />
          </div>
          <p className="mt-2 break-all font-mono text-xs text-muted-foreground">
            {trace.id}
          </p>
          {trace.attempt ? (
            <p className="mt-1 text-xs text-muted-foreground">
              第 {trace.attempt} 次尝试
            </p>
          ) : null}
        </div>
        <div className="text-right">
          <p className="font-mono text-2xl font-semibold">
            {Math.round(trace.total_latency_ms)} ms
          </p>
          <p className="text-xs text-muted-foreground">总耗时</p>
        </div>
      </div>
      {trace.error ? (
        <div className="mt-5 rounded-md border border-danger/20 bg-danger/10 p-3 text-sm text-danger">
          <p className="font-semibold">处理失败</p>
          <p className="mt-1">{trace.error}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <p className="font-mono text-xs">Trace ID: {trace.id}</p>
            <button
              className="text-xs font-semibold underline"
              onClick={() => void navigator.clipboard.writeText(trace.id)}
              type="button"
            >
              复制 Trace ID
            </button>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            请检查失败阶段的错误码和安全摘要；修复配置或源文件后再重试。
          </p>
        </div>
      ) : null}
      {trace.trace_type === "ingestion" &&
      (trace.retryable || trace.cancelable) ? (
        <div className="mt-4 flex gap-2">
          {trace.retryable ? (
            <Button
              loading={actionPending === "retry"}
              onClick={() => void retry()}
              variant="secondary"
            >
              重试任务
            </Button>
          ) : null}
          {trace.cancelable ? (
            <Button
              loading={actionPending === "cancel"}
              onClick={() => void cancel()}
              variant="secondary"
            >
              取消任务
            </Button>
          ) : null}
        </div>
      ) : null}
      {stages.length ? (
        <div className="mt-7 space-y-4">
          {stages.map((stage, index) => {
            const visual = visuals[stage.name] ?? {
              icon: Clock3,
              color: "bg-muted-foreground",
            };
            const Icon = visual.icon;
            const description = traceStageDescription(stage);
            const skippedStage = stage.details?.event === "skipped";
            return (
              <article
                className="relative grid gap-4 rounded-lg border border-border bg-surface p-4 md:grid-cols-[2.5rem_minmax(0,1fr)_8rem]"
                key={`${stage.name}-${index}`}
              >
                <span
                  className={`grid size-10 place-items-center rounded-md text-white ${visual.color}`}
                >
                  <Icon className="size-4" />
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-semibold">{traceStageLabel(stage)}</h3>
                    {shouldShowRawStageName(stage) ? (
                      <span className="rounded bg-surface-muted px-2 py-0.5 font-mono text-[0.6875rem]">
                        {stage.name}
                      </span>
                    ) : null}
                    {stage.method ? (
                      <span className="rounded bg-surface-muted px-2 py-0.5 font-mono text-[0.6875rem]">
                        {stage.method}
                      </span>
                    ) : null}
                    {stage.provider ? (
                      <span className="text-xs text-muted-foreground">
                        {stage.provider}
                      </span>
                    ) : null}
                  </div>
                  {description ? (
                    <p className="mt-1 text-sm text-muted-foreground">
                      {description}
                    </p>
                  ) : null}
                  <p className="mt-2 text-xs text-muted-foreground">
                    {new Intl.DateTimeFormat("zh-CN", {
                      dateStyle: "medium",
                      timeStyle: "medium",
                    }).format(new Date(stage.started_at))}
                  </p>
                  {stage.input_count != null || stage.output_count != null ? (
                    <p className="mt-2 text-xs text-muted-foreground">
                      输入 {stage.input_count ?? "—"} · 输出{" "}
                      {stage.output_count ?? "—"} · 尝试{" "}
                      {stage.attempt ?? trace.attempt ?? 0}
                    </p>
                  ) : null}
                  {stage.skip_reason ? (
                    <p className="mt-2 rounded-md bg-warning/10 p-2 text-xs text-warning">
                      跳过原因：{stage.skip_reason}
                    </p>
                  ) : null}
                  {stage.error_code || stage.error_summary ? (
                    <div className="mt-2 rounded-md border border-danger/20 bg-danger/5 p-2 text-xs">
                      <p className="font-mono font-semibold text-danger">
                        {stage.error_code ?? "STAGE_FAILED"}
                      </p>
                      <p className="mt-1 text-muted-foreground">
                        {stage.error_summary ??
                          "该阶段执行失败，请根据错误码检查服务配置。"}
                      </p>
                    </div>
                  ) : null}
                </div>
                <div className="font-mono text-sm font-semibold md:text-right">
                  {skippedStage ? "—" : `${stage.duration_ms.toFixed(1)} ms`}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="mt-6">
          <EmptyState
            description="该任务存在，但后端没有保存阶段事件；这通常是旧任务或尚未产生 Trace 的文档处理记录。"
            icon={Clock3}
            title="没有阶段记录"
          />
        </div>
      )}
      <dl className="mt-6 grid gap-3 border-t border-border pt-5 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">开始时间</dt>
          <dd className="mt-1 font-mono">
            {new Date(trace.started_at).toLocaleString("zh-CN")}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">结束时间</dt>
          <dd className="mt-1 font-mono">
            {new Date(trace.finished_at).toLocaleString("zh-CN")}
          </dd>
        </div>
      </dl>
    </section>
  );
}
