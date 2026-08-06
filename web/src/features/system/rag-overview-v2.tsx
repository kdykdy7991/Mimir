"use client";

import { useCallback, useState, type ComponentType, type CSSProperties } from "react";
import { Activity, Boxes, Clock3, FileStack, Gauge, Layers3, SearchCheck, Timer, WalletCards } from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { useApiResource } from "@/api/use-api-resource";
import { ErrorState, LoadingState, StatusBadge } from "@/components";
import type { OverviewResponse } from "@/types";

type Range = "24h" | "7d" | "30d";
const ranges: { value: Range; label: string }[] = [
  { value: "24h", label: "24 小时" },
  { value: "7d", label: "7 天" },
  { value: "30d", label: "30 天" },
];

function percent(value: number | null | undefined) {
  return value == null ? "暂无数据" : `${value.toFixed(1)}%`;
}

function latency(value: number | null | undefined) {
  if (value == null) return "暂无数据";
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`;
}

function compact(value: number | null | undefined) {
  if (value == null) return "未接入";
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function TrafficCard({ icon: Icon, label, value, note }: { icon: ComponentType<{ className?: string }>; label: string; value: string; note: string }) {
  return (
    <article className="glass-surface rounded-xl p-5 sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        <Icon className="size-4 text-primary" />
      </div>
      <p className="mt-4 text-3xl font-semibold tracking-[-0.04em]">{value}</p>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">{note}</p>
    </article>
  );
}

function Trend({ data }: { data: OverviewResponse }) {
  const maximum = Math.max(1, ...data.trend.map((point) => point.query_count));
  const dateLabel = (timestamp: string) => new Intl.DateTimeFormat(
    "zh-CN",
    data.range === "24h" ? { hour: "2-digit", minute: "2-digit" } : { month: "numeric", day: "numeric" },
  ).format(new Date(timestamp));

  return (
    <section className="glass-surface rounded-xl p-5 sm:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="font-mono text-[0.6875rem] font-semibold tracking-[0.14em] text-primary uppercase">Trend</p>
          <h2 className="mt-2 text-xl font-semibold">请求趋势</h2>
        </div>
        <p className="text-xs text-muted-foreground">共 {data.traffic.request_count.toLocaleString()} 次请求</p>
      </div>
      <div className="mt-8 grid h-48 items-end gap-2 border-b border-border px-1" style={{ gridTemplateColumns: `repeat(${data.trend.length}, minmax(0, 1fr))` }}>
        {data.trend.map((point) => (
          <div className="group relative flex h-full items-end justify-center" key={point.timestamp}>
            <div className="absolute bottom-[calc(var(--height)+0.5rem)] hidden rounded bg-foreground px-2 py-1 text-[0.625rem] text-background group-hover:block">{point.query_count} 次</div>
            <div
              className="w-full max-w-14 rounded-t-md bg-primary/70 transition-colors group-hover:bg-primary"
              style={{ height: `${Math.max(3, point.query_count / maximum * 100)}%`, "--height": `${Math.max(3, point.query_count / maximum * 100)}%` } as CSSProperties}
            />
          </div>
        ))}
      </div>
      <div className="mt-3 grid gap-2 text-center text-[0.625rem] text-muted-foreground" style={{ gridTemplateColumns: `repeat(${data.trend.length}, minmax(0, 1fr))` }}>
        {data.trend.map((point) => <span key={`label-${point.timestamp}`}>{dateLabel(point.timestamp)}</span>)}
      </div>
    </section>
  );
}

function RetrievalHealth({ data }: { data: OverviewResponse["retrieval_health"] }) {
  const items = [
    { label: "检索成功率", value: percent(data.success_rate), detail: "至少召回一个 Chunk" },
    { label: "空召回率", value: percent(data.empty_retrieval_rate), detail: "未召回任何结果" },
    { label: "平均 TopK", value: data.average_top_k == null ? "暂无数据" : data.average_top_k.toFixed(1), detail: "每次请求平均返回数量" },
    { label: "检索耗时", value: latency(data.average_latency_ms), detail: "平均端到端检索耗时" },
  ];
  return (
    <section className="glass-surface rounded-xl p-5 sm:p-6">
      <div>
        <p className="font-mono text-[0.6875rem] font-semibold tracking-[0.14em] text-primary uppercase">Retrieval Health</p>
        <h2 className="mt-2 text-xl font-semibold">检索健康度</h2>
        <p className="mt-1 text-sm text-muted-foreground">关注召回是否稳定、结果规模是否合理</p>
      </div>
      <dl className="mt-6 divide-y divide-border">
        {items.map((item) => (
          <div className="grid gap-2 py-4 first:pt-0 last:pb-0 sm:grid-cols-[1fr_auto] sm:items-center" key={item.label}>
            <div><dt className="text-sm font-medium">{item.label}</dt><p className="mt-1 text-xs text-muted-foreground">{item.detail}</p></div>
            <dd className="text-2xl font-semibold tracking-[-0.03em] tabular-nums">{item.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function relativeTime(timestamp: string | null | undefined) {
  if (!timestamp) return "暂无记录";
  const seconds = Math.max(0, (Date.now() - new Date(timestamp).getTime()) / 1000);
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86_400)} 天前`;
}

type KnowledgeBaseView = Omit<OverviewResponse["knowledge_base_health"], "index_status"> & { index_status: OverviewResponse["knowledge_base_health"]["index_status"] | "unavailable" };

function KnowledgeBase({ data }: { data: KnowledgeBaseView }) {
  const status = data.index_status === "ready" ? { label: "索引就绪", tone: "ok" as const } : data.index_status === "indexing" ? { label: "索引处理中", tone: "pending" as const } : data.index_status === "attention" ? { label: "需要关注", tone: "degraded" as const } : { label: "状态未提供", tone: "pending" as const };
  return (
    <section className="glass-surface rounded-xl p-5 sm:p-6">
      <div>
        <p className="font-mono text-[0.6875rem] font-semibold tracking-[0.14em] text-primary uppercase">Knowledge Base</p>
        <h2 className="mt-2 text-xl font-semibold">知识库状态</h2>
        <p className="mt-1 text-sm text-muted-foreground">语料规模和索引新鲜度</p>
      </div>
      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg border border-border bg-surface-muted/40 p-4"><FileStack className="size-4 text-primary" /><p className="mt-4 text-2xl font-semibold">{data.document_count.toLocaleString()}</p><p className="mt-1 text-xs text-muted-foreground">文档数</p></div>
        <div className="rounded-lg border border-border bg-surface-muted/40 p-4"><Layers3 className="size-4 text-primary" /><p className="mt-4 text-2xl font-semibold">{compact(data.chunk_count)}</p><p className="mt-1 text-xs text-muted-foreground">Chunk 数</p></div>
        <div className="rounded-lg border border-border bg-surface-muted/40 p-4"><Gauge className="size-4 text-primary" /><div className="mt-4"><StatusBadge label={status.label} status={status.tone} /></div><p className="mt-2 text-xs text-muted-foreground">索引状态</p></div>
        <div className="rounded-lg border border-border bg-surface-muted/40 p-4"><Clock3 className="size-4 text-primary" /><p className="mt-4 text-lg font-semibold">{relativeTime(data.last_updated_at)}</p><p className="mt-1 text-xs text-muted-foreground">最近更新时间</p></div>
      </div>
    </section>
  );
}

export function RagOverviewV2() {
  const [range, setRange] = useState<Range>("7d");
  const load = useCallback((signal: AbortSignal) => apiClient.getOverview(range, signal), [range]);
  const { data, error, loading, retry } = useApiResource(load);
  if (loading && !data) return <LoadingState label="正在计算 RAG 指标" rows={6} />;
  if (error || !data) {
    const apiError = error instanceof ApiError ? error : undefined;
    return <ErrorState {...(apiError?.code ? { code: apiError.code } : {})} {...(error instanceof Error ? { description: error.message } : {})} onRetry={retry} title="无法读取 RAG 总览" />;
  }

  const runtime = data as unknown as {
    traffic?: OverviewResponse["traffic"];
    retrieval_health?: OverviewResponse["retrieval_health"];
    knowledge_base_health?: OverviewResponse["knowledge_base_health"];
  };
  const legacyResponse = !runtime.traffic || !runtime.retrieval_health || !runtime.knowledge_base_health;
  const traffic = runtime.traffic ?? {
    request_count: data.query_count ?? 0,
    previous_request_count: data.previous_query_count ?? 0,
    success_rate: null,
    average_latency_ms: null,
    embedding_token_usage: null,
  };
  const legacyNoResultRate = data.metrics.no_result_rate.value ?? null;
  const retrievalHealth = runtime.retrieval_health ?? {
    success_rate: legacyNoResultRate == null ? null : 100 - legacyNoResultRate,
    empty_retrieval_rate: legacyNoResultRate,
    average_top_k: null,
    average_latency_ms: null,
    rerank_success_rate: null,
  };
  const knowledgeBase: KnowledgeBaseView = runtime.knowledge_base_health ?? {
    document_count: data.corpus.document_count,
    chunk_count: data.corpus.chunk_count,
    index_status: "unavailable",
    last_updated_at: null,
  };
  const viewData = { ...data, traffic };

  return (
    <div className="space-y-6">
      <header className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
        <div><div className="flex items-center gap-2"><Boxes className="size-5 text-primary" /><span className="font-mono text-xs font-semibold tracking-[0.14em] text-primary uppercase">RAG Overview</span></div><h1 className="mt-3 text-3xl font-semibold tracking-[-0.04em] sm:text-4xl">运行总览</h1><p className="mt-2 text-muted-foreground">请求负载、检索质量和知识库状态。</p>{legacyResponse ? <p className="mt-2 text-xs text-warning">后端仍在返回旧版指标，重启后端后将显示完整数据。</p> : null}</div>
        <div aria-label="统计周期" className="inline-flex self-start rounded-lg border border-border bg-surface p-1">
          {ranges.map((item) => <button className={`rounded-md px-3 py-2 text-xs font-medium ${range === item.value ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`} key={item.value} onClick={() => setRange(item.value)} type="button">{item.label}</button>)}
        </div>
      </header>

      <section>
        <div className="mb-3 flex items-center gap-2"><Activity className="size-4 text-primary" /><h2 className="text-sm font-semibold">Traffic</h2></div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <TrafficCard icon={Activity} label="请求量" note={`上一周期 ${traffic.previous_request_count.toLocaleString()} 次`} value={compact(traffic.request_count)} />
          <TrafficCard icon={SearchCheck} label="成功率" note="已产生有效查询结果的请求占比" value={percent(traffic.success_rate)} />
          <TrafficCard icon={Timer} label="平均延迟" note="RAG 检索端到端平均耗时" value={latency(traffic.average_latency_ms)} />
          <TrafficCard icon={WalletCards} label="Embedding Token" note="查询与文档索引的向量化输入 Token" value={compact(traffic.embedding_token_usage)} />
        </div>
      </section>

      <Trend data={viewData} />
      <div className="grid gap-6 lg:grid-cols-2">
        <RetrievalHealth data={retrievalHealth} />
        <KnowledgeBase data={knowledgeBase} />
      </div>
    </div>
  );
}
