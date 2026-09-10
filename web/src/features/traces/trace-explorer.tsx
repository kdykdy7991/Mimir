"use client";

import { FormEvent, useEffect, useState } from "react";
import { Braces, Clock3, DatabaseZap, FileInput, GitMerge, Layers3, Network, Search, Sparkles, type LucideIcon } from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { EmptyState, ErrorState, LoadingState, StatusBadge } from "@/components";
import type { TraceResponse } from "@/types";
import { isSkippedTrace, shouldShowRawStageName, traceStageDescription, traceStageLabel } from "./trace-presentation";

type TraceType = "query" | "ingestion";
const visuals: Record<string, { icon: LucideIcon; color: string }> = {
  dense: { icon: Network, color: "bg-primary" }, dense_retrieval: { icon: Network, color: "bg-primary" },
  sparse: { icon: Braces, color: "bg-accent" }, sparse_retrieval: { icon: Braces, color: "bg-accent" },
  fusion: { icon: GitMerge, color: "bg-info" }, rerank: { icon: Sparkles, color: "bg-success" },
  load: { icon: FileInput, color: "bg-primary" }, split: { icon: Layers3, color: "bg-accent" },
  transform: { icon: Braces, color: "bg-info" }, embed: { icon: DatabaseZap, color: "bg-warning" },
  upsert: { icon: DatabaseZap, color: "bg-success" }, query_processing: { icon: Search, color: "bg-primary" },
  trim: { icon: Layers3, color: "bg-success" },
};

export function TraceExplorer({ initialId = "", initialType = "query" }: { initialId?: string; initialType?: TraceType }) {
  const [id, setId] = useState(initialId);
  const [type, setType] = useState<TraceType>(initialType);
  const [trace, setTrace] = useState<TraceResponse>();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(Boolean(initialId));

  useEffect(() => {
    if (!initialId) return;
    let cancelled = false;
    const request = initialType === "query" ? apiClient.getQueryTrace(initialId) : apiClient.getIngestionTrace(initialId);
    request.then((value) => { if (!cancelled) setTrace(value); }).catch((reason: unknown) => { if (!cancelled) setError(reason); }).finally(() => { if (!cancelled) setPending(false); });
    return () => { cancelled = true; };
  }, [initialId, initialType]);

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault(); const value = id.trim(); if (!value) return;
    setPending(true); setError(undefined); setTrace(undefined);
    try { setTrace(type === "query" ? await apiClient.getQueryTrace(value) : await apiClient.getIngestionTrace(value)); }
    catch (reason) { setError(reason); } finally { setPending(false); }
  }

  const apiError = error instanceof ApiError ? error : undefined;
  return <div className="space-y-6">
    <form className="glass-surface rounded-xl p-5" onSubmit={submit}><div className="grid gap-4 lg:grid-cols-[12rem_minmax(0,1fr)_auto] lg:items-end"><label className="text-xs font-semibold text-muted-foreground">Trace 类型<select className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3 text-sm" onChange={(event) => setType(event.target.value as TraceType)} value={type}><option value="query">Query</option><option value="ingestion">Ingestion</option></select></label><label className="text-xs font-semibold text-muted-foreground">Trace ID<input className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3 font-mono text-sm outline-none focus:border-primary" onChange={(event) => setId(event.target.value)} placeholder={type === "query" ? "输入 query_id" : "输入上传返回的 task_id"} value={id} /></label><button className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground disabled:opacity-45" disabled={pending || !id.trim()} type="submit"><Search className="size-4" />{pending ? "查询中…" : "读取 Trace"}</button></div><p className="mt-3 text-xs text-muted-foreground">当前 API 不提供 Trace 列表：Query 使用 query_id，Ingestion 使用上传任务的 task_id。</p></form>

    {pending ? <LoadingState label="正在读取 Trace" rows={7} /> : error ? <ErrorState {...(apiError ? { code: apiError.code, ...(apiError.requestId ? { requestId: apiError.requestId } : {}) } : {})} description={apiError?.code === "QUERY_NOT_FOUND" || apiError?.code === "INGESTION_NOT_FOUND" ? "没有找到对应 Trace，请确认类型与 ID 是否匹配。" : error instanceof Error ? error.message : "Trace 读取失败"} onRetry={() => void submit()} title="无法读取 Trace" /> : trace ? <TraceDetails trace={trace} /> : <EmptyState description="从 Playground 查询结果进入时会自动携带 Query Trace ID；也可以手动输入 Query ID 或 Task ID。" icon={Clock3} title="输入 ID 查看执行链路" />}
  </div>;
}

function TraceDetails({ trace }: { trace: TraceResponse }) {
  const stages = trace.stages ?? [];
  const skipped = isSkippedTrace(trace);
  return <section className="glass-surface rounded-xl p-5 sm:p-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex items-center gap-2"><h2 className="text-lg font-semibold">{trace.trace_type === "query" ? "查询链路" : "文档处理链路"}</h2><StatusBadge label={trace.error ? "存在错误" : skipped ? "已跳过" : "已记录"} status={trace.error ? "failed" : skipped ? "skipped" : "ok"} /></div><p className="mt-2 break-all font-mono text-xs text-muted-foreground">{trace.id}</p></div><div className="text-right"><p className="font-mono text-2xl font-semibold">{Math.round(trace.total_latency_ms)} ms</p><p className="text-xs text-muted-foreground">总耗时</p></div></div>{trace.error ? <p className="mt-5 rounded-md bg-danger/10 p-3 text-sm text-danger">{trace.error}</p> : null}{stages.length ? <div className="mt-7 space-y-4">{stages.map((stage, index) => { const visual = visuals[stage.name] ?? { icon: Clock3, color: "bg-muted-foreground" }; const Icon = visual.icon; const description = traceStageDescription(stage); const skippedStage = stage.details?.event === "skipped"; return <article className="relative grid gap-4 rounded-lg border border-border bg-surface p-4 md:grid-cols-[2.5rem_minmax(0,1fr)_8rem]" key={`${stage.name}-${index}`}><span className={`grid size-10 place-items-center rounded-md text-white ${visual.color}`}><Icon className="size-4" /></span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{traceStageLabel(stage)}</h3>{shouldShowRawStageName(stage) ? <span className="rounded bg-surface-muted px-2 py-0.5 font-mono text-[0.6875rem]">{stage.name}</span> : null}{stage.method ? <span className="rounded bg-surface-muted px-2 py-0.5 font-mono text-[0.6875rem]">{stage.method}</span> : null}{stage.provider ? <span className="text-xs text-muted-foreground">{stage.provider}</span> : null}</div>{skippedStage && description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}<p className="mt-2 text-xs text-muted-foreground">{new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "medium" }).format(new Date(stage.started_at))}</p>{stage.details && Object.keys(stage.details).length ? <pre className="mt-3 overflow-x-auto rounded-md bg-surface-muted p-3 font-mono text-[0.6875rem] text-muted-foreground">{JSON.stringify(stage.details, null, 2)}</pre> : null}</div><div className="font-mono text-sm font-semibold md:text-right">{skippedStage ? "—" : `${stage.duration_ms.toFixed(1)} ms`}</div></article>; })}</div> : <div className="mt-6"><EmptyState description="该任务存在，但后端没有保存阶段事件；这通常是旧任务或尚未产生 Trace 的文档处理记录。" icon={Clock3} title="没有阶段记录" /></div>}<dl className="mt-6 grid gap-3 border-t border-border pt-5 text-xs sm:grid-cols-2"><div><dt className="text-muted-foreground">开始时间</dt><dd className="mt-1 font-mono">{new Date(trace.started_at).toLocaleString("zh-CN")}</dd></div><div><dt className="text-muted-foreground">结束时间</dt><dd className="mt-1 font-mono">{new Date(trace.finished_at).toLocaleString("zh-CN")}</dd></div></dl></section>;
}
