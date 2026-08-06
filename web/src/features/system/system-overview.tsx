"use client";

import { useCallback } from "react";
import { Boxes, FileStack, HeartPulse, Layers3, Network } from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { useApiResource } from "@/api/use-api-resource";
import { ErrorState, LoadingState, StatusBadge } from "@/components";

export function SystemOverview() {
  const load = useCallback((signal: AbortSignal) => Promise.all([
    apiClient.getSystemInfo(signal), apiClient.getSystemHealth(signal),
    apiClient.listCollections({ limit: 100 }, signal),
  ]), []);
  const { data, error, loading, retry } = useApiResource(load);
  if (loading) return <LoadingState label="正在读取系统状态" rows={4} />;
  if (error || !data) {
    const apiError = error instanceof ApiError ? error : undefined;
    return <ErrorState {...(apiError?.code ? { code: apiError.code } : {})} {...(error instanceof Error ? { description: error.message } : {})} onRetry={retry} {...(apiError?.requestId ? { requestId: apiError.requestId } : {})} title="无法读取系统状态" />;
  }
  const [info, health, collections] = data;
  const documents = collections.items.reduce((sum, item) => sum + (item.document_count ?? 0), 0);
  const chunks = collections.items.reduce((sum, item) => sum + (item.chunk_count ?? 0), 0);
  const metrics = [
    { label: "知识库", value: collections.items.length, icon: Boxes },
    { label: "文档", value: documents, icon: FileStack },
    { label: "可检索片段", value: chunks.toLocaleString(), icon: Layers3 },
    { label: "服务版本", value: info.version, icon: HeartPulse },
  ];
  return <div className="space-y-6">
    <section aria-label="实时系统指标" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map(({ icon: Icon, label, value }) => <article className="glass-surface rounded-lg p-5" key={label}><div className="flex items-start justify-between gap-4"><div><p className="text-sm font-medium text-muted-foreground">{label}</p><p className="mt-2 text-3xl font-semibold tracking-[-0.04em]">{value}</p></div><span className="grid size-10 place-items-center rounded-md bg-primary/10 text-primary"><Icon aria-hidden="true" className="size-5" /></span></div></article>)}
    </section>
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="glass-surface rounded-xl p-5 sm:p-6"><div className="flex items-start justify-between"><div><h2 className="text-lg font-semibold">检索架构</h2><p className="mt-1 text-sm text-muted-foreground">System API 返回的当前后端配置</p></div><StatusBadge label={health.status === "ok" ? "服务正常" : "服务异常"} status={health.status} /></div><dl className="mt-5 grid gap-3 sm:grid-cols-3">{[["向量存储", info.storage_backend], ["稀疏检索", info.sparse_backend], ["精排", info.rerank_backend]].map(([label, value]) => <div className="rounded-lg border border-border bg-surface-muted/60 p-4" key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-2 font-mono text-sm font-semibold">{value}</dd></div>)}</dl></section>
      <section className="glass-surface rounded-xl p-5 sm:p-6"><div className="flex items-start justify-between"><div><h2 className="text-lg font-semibold">Provider 状态</h2><p className="mt-1 text-sm text-muted-foreground">只展示配置状态，不展示凭据</p></div><Network aria-hidden="true" className="size-5 text-muted-foreground" /></div><div className="mt-5 divide-y divide-border">{Object.entries(info.providers).map(([name, provider]) => <div className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0" key={name}><div className="min-w-0"><p className="font-semibold capitalize">{name}</p><p className="mt-1 truncate font-mono text-xs text-muted-foreground">{provider.model ?? "未指定模型"}</p></div><StatusBadge label={!provider.configured ? "未配置" : provider.ready ? "已配置" : "未就绪"} status={!provider.configured ? "pending" : provider.ready ? "ok" : "degraded"} /></div>)}</div></section>
    </div>
  </div>;
}
