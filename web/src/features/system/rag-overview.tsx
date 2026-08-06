"use client";

import Link from "next/link";
import { useCallback, useState, type ComponentType } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  FileCheck2,
  SearchCheck,
  Timer,
} from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { useApiResource } from "@/api/use-api-resource";
import { ErrorState, LoadingState, StatusBadge } from "@/components";
import type { OverviewResponse } from "@/types";

type Range = "24h" | "7d" | "30d";
type Metric = OverviewResponse["metrics"]["effective_retrieval_rate"];

const RANGE_LABELS: Record<Range, string> = {
  "24h": "24 小时",
  "7d": "7 天",
  "30d": "30 天",
};

function formatPercent(value: number | null | undefined) {
  return value == null ? "暂无数据" : `${value.toFixed(1)}%`;
}

function formatLatency(value: number | null | undefined) {
  if (value == null) return "暂无数据";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`;
}

function formatCount(value: number | null | undefined) {
  return value == null ? "暂无数据" : Math.round(value).toLocaleString();
}

function MetricCard({
  better,
  description,
  format,
  icon: Icon,
  label,
  metric,
}: {
  better: "higher" | "lower";
  description: string;
  format: (value: number | null | undefined) => string;
  icon: ComponentType<{ className?: string }>;
  label: string;
  metric: Metric;
}) {
  const hasComparison = metric.value != null && metric.previous != null;
  const delta = hasComparison ? metric.value! - metric.previous! : null;
  const improved = delta != null && (better === "higher" ? delta > 0 : delta < 0);
  const DeltaIcon = delta != null && delta > 0 ? ArrowUpRight : ArrowDownRight;

  return (
    <article className="glass-surface rounded-xl p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          <p className="mt-2 text-3xl font-semibold tracking-[-0.04em]">{format(metric.value)}</p>
        </div>
        <span className="grid size-10 place-items-center rounded-lg bg-primary/10 text-primary">
          <Icon className="size-5" />
        </span>
      </div>
      <div className="mt-4 flex min-h-5 items-center gap-2 text-xs">
        {delta == null || delta === 0 ? (
          <span className="text-muted-foreground">{metric.value == null ? "当前周期尚无样本" : "与上一周期持平"}</span>
        ) : (
          <span className={`inline-flex items-center gap-1 font-medium ${improved ? "text-success" : "text-warning"}`}>
            <DeltaIcon className="size-3.5" />
            {format(Math.abs(delta))}
            <span className="font-normal text-muted-foreground">较上一周期</span>
          </span>
        )}
      </div>
      <p className="mt-3 border-t border-border pt-3 text-xs leading-5 text-muted-foreground">{description}</p>
    </article>
  );
}

function TrendPanel({ data }: { data: OverviewResponse }) {
  const latencyMax = Math.max(2000, ...data.trend.map((point) => point.p95_latency_ms ?? 0));
  const label = (value: string) => new Intl.DateTimeFormat("zh-CN", data.range === "24h" ? { hour: "2-digit" } : { month: "numeric", day: "numeric" }).format(new Date(value));

  return (
    <section className="glass-surface min-w-0 rounded-xl p-5 sm:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">质量与响应趋势</h2>
          <p className="mt-1 text-sm text-muted-foreground">观察质量下降是否同时伴随响应变慢</p>
        </div>
        <p className="text-xs text-muted-foreground">共 {data.query_count.toLocaleString()} 次查询</p>
      </div>

      <div className="mt-7">
        <div className="mb-3 flex items-center justify-between text-xs">
          <span className="font-medium">有效检索率</span>
          <span className="text-muted-foreground">目标 ≥ 90%</span>
        </div>
        <div className="grid h-28 items-end gap-2 border-b border-border px-1" style={{ gridTemplateColumns: `repeat(${data.trend.length}, minmax(0, 1fr))` }}>
          {data.trend.map((point) => (
            <div className="group relative flex h-full items-end justify-center" key={`quality-${point.timestamp}`}>
              <div
                className={`w-full max-w-10 rounded-t-sm transition-colors ${point.effective_retrieval_rate == null ? "bg-surface-muted" : point.effective_retrieval_rate >= 90 ? "bg-success/70" : "bg-warning/70"}`}
                style={{ height: `${Math.max(4, point.effective_retrieval_rate ?? 4)}%` }}
                title={point.effective_retrieval_rate == null ? "暂无查询" : `${point.effective_retrieval_rate.toFixed(1)}%`}
              />
            </div>
          ))}
        </div>
        <div className="mt-2 grid gap-2 text-center text-[0.625rem] text-muted-foreground" style={{ gridTemplateColumns: `repeat(${data.trend.length}, minmax(0, 1fr))` }}>
          {data.trend.map((point) => <span key={`quality-label-${point.timestamp}`}>{label(point.timestamp)}</span>)}
        </div>
      </div>

      <div className="mt-8">
        <div className="mb-3 flex items-center justify-between text-xs">
          <span className="font-medium">P95 响应时间</span>
          <span className="text-muted-foreground">目标 ≤ 2 秒</span>
        </div>
        <div className="grid h-20 items-end gap-2 border-b border-border px-1" style={{ gridTemplateColumns: `repeat(${data.trend.length}, minmax(0, 1fr))` }}>
          {data.trend.map((point) => {
            const height = point.p95_latency_ms == null ? 4 : Math.max(6, point.p95_latency_ms / latencyMax * 100);
            return <div className="flex h-full items-end justify-center" key={`latency-${point.timestamp}`}><div className={`w-full max-w-10 rounded-t-sm ${point.p95_latency_ms == null ? "bg-surface-muted" : point.p95_latency_ms <= 2000 ? "bg-primary/60" : "bg-warning/70"}`} style={{ height: `${height}%` }} title={formatLatency(point.p95_latency_ms)} /></div>;
          })}
        </div>
      </div>
    </section>
  );
}

function AttentionPanel({ items }: { items: OverviewResponse["attention"] }) {
  return (
    <section className="glass-surface rounded-xl p-5 sm:p-6">
      <div>
        <h2 className="text-lg font-semibold">需要处理</h2>
        <p className="mt-1 text-sm text-muted-foreground">只展示会影响检索体验的事项</p>
      </div>
      {items.length ? (
        <div className="mt-5 divide-y divide-border">
          {items.map((item, index) => (
            <Link className="group flex gap-3 py-4 first:pt-0 last:pb-0" href={item.href} key={`${item.title}-${index}`}>
              <span className={`mt-0.5 grid size-8 shrink-0 place-items-center rounded-md ${item.severity === "critical" ? "bg-danger/10 text-danger" : "bg-warning/10 text-warning"}`}>
                <AlertTriangle className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold">{item.title}</span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">{item.description}</span>
              </span>
              <ArrowRight className="mt-2 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
            </Link>
          ))}
        </div>
      ) : (
        <div className="mt-6 rounded-lg border border-success/20 bg-success/5 p-5 text-center">
          <CheckCircle2 className="mx-auto size-6 text-success" />
          <p className="mt-3 text-sm font-medium">暂无需要处理的异常</p>
          <p className="mt-1 text-xs text-muted-foreground">质量、延迟和文档处理均在目标范围内。</p>
        </div>
      )}
    </section>
  );
}

function KnowledgeBaseRisks({ rows }: { rows: OverviewResponse["knowledge_bases"] }) {
  return (
    <section className="glass-surface overflow-hidden rounded-xl">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border p-5 sm:p-6">
        <div>
          <h2 className="text-lg font-semibold">需要关注的知识库</h2>
          <p className="mt-1 text-sm text-muted-foreground">按响应延迟和查询量排序</p>
        </div>
        <Link className="inline-flex items-center gap-1 text-xs font-semibold text-primary" href="/collections">查看全部<ArrowRight className="size-3.5" /></Link>
      </div>
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[34rem] text-left text-sm">
            <thead className="bg-surface-muted/60 text-xs text-muted-foreground">
              <tr><th className="px-5 py-3 font-medium">知识库</th><th className="px-5 py-3 font-medium">查询量</th><th className="px-5 py-3 font-medium">P95 延迟</th><th className="px-5 py-3 font-medium">状态</th></tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row) => <tr key={row.collection_id}><td className="px-5 py-4"><Link className="font-semibold hover:text-primary" href={`/collections/${row.collection_id}`}>{row.name}</Link></td><td className="px-5 py-4 tabular-nums">{row.query_count.toLocaleString()}</td><td className="px-5 py-4 tabular-nums">{formatLatency(row.p95_latency_ms)}</td><td className="px-5 py-4"><StatusBadge label={row.status === "ok" ? "正常" : "需关注"} status={row.status === "ok" ? "ok" : "degraded"} /></td></tr>)}
            </tbody>
          </table>
        </div>
      ) : <div className="p-8 text-center text-sm text-muted-foreground">当前周期暂无知识库查询数据。</div>}
    </section>
  );
}

export function RagOverview() {
  const [range, setRange] = useState<Range>("7d");
  const load = useCallback((signal: AbortSignal) => apiClient.getOverview(range, signal), [range]);
  const { data, error, loading, retry } = useApiResource(load);

  if (loading && !data) return <LoadingState label="正在计算 RAG 运行指标" rows={6} />;
  if (error || !data) {
    const apiError = error instanceof ApiError ? error : undefined;
    return <ErrorState {...(apiError?.code ? { code: apiError.code } : {})} {...(error instanceof Error ? { description: error.message } : {})} onRetry={retry} {...(apiError?.requestId ? { requestId: apiError.requestId } : {})} title="无法读取总览指标" />;
  }

  const statusTone = data.status === "ok" ? "success" : data.status === "critical" ? "danger" : "warning";
  const queryMetric: Metric = { value: data.query_count, previous: data.previous_query_count, target: null };
  return (
    <div className="space-y-6">
      <section className={`rounded-2xl border p-6 sm:p-8 ${data.status === "ok" ? "border-success/20 bg-success/5" : data.status === "critical" ? "border-danger/20 bg-danger/5" : "border-warning/20 bg-warning/5"}`}>
        <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-start">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2"><StatusBadge label={data.status === "ok" ? "运行正常" : data.status === "critical" ? "存在异常" : "需要关注"} status={statusTone} /><span className="text-xs text-muted-foreground">最近 {RANGE_LABELS[range]}</span></div>
            <h1 className="mt-4 text-3xl font-semibold tracking-[-0.04em] sm:text-4xl">{data.headline}</h1>
            <p className="mt-3 text-sm leading-6 text-muted-foreground sm:text-base">{data.summary}</p>
            <p className="mt-5 text-xs text-muted-foreground">{data.corpus.collection_count} 个知识库 · {data.corpus.document_count.toLocaleString()} 份文档 · {data.corpus.chunk_count.toLocaleString()} 个可检索片段</p>
          </div>
          <div aria-label="统计周期" className="inline-flex self-start rounded-lg border border-border bg-surface/80 p-1">
            {(Object.keys(RANGE_LABELS) as Range[]).map((item) => <button className={`rounded-md px-3 py-2 text-xs font-medium transition-colors ${range === item ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`} key={item} onClick={() => setRange(item)} type="button">{RANGE_LABELS[item]}</button>)}
          </div>
        </div>
      </section>

      <section aria-label="RAG 核心指标" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard better="higher" description="当前统计周期内实际执行的 RAG 查询总量" format={formatCount} icon={Activity} label="查询次数" metric={queryMetric} />
        <MetricCard better="higher" description="有检索结果且未发生降级的查询占比" format={formatPercent} icon={SearchCheck} label="有效检索率" metric={data.metrics.effective_retrieval_rate} />
        <MetricCard better="lower" description="95% 的查询能在该时间内完成" format={formatLatency} icon={Timer} label="P95 响应时间" metric={data.metrics.p95_latency_ms} />
        <MetricCard better="higher" description="已结束的文档任务中成功进入索引的占比" format={formatPercent} icon={FileCheck2} label="文档处理成功率" metric={data.metrics.ingestion_success_rate} />
      </section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.65fr)_minmax(20rem,0.85fr)]">
        <TrendPanel data={data} />
        <AttentionPanel items={data.attention} />
      </div>
      <KnowledgeBaseRisks rows={data.knowledge_bases} />
    </div>
  );
}
