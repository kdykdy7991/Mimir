"use client";

import Image from "next/image";
import Link from "next/link";
import { FormEvent, useCallback, useState } from "react";
import { ArrowUpRight, ChevronDown, Clock3, Database, FileText, Gauge, ImageIcon, Layers3, Search, Send, Settings2, SlidersHorizontal, Sparkles } from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { useApiResource } from "@/api/use-api-resource";
import { EmptyState, ErrorState, LoadingState, StatusBadge } from "@/components";
import { cn } from "@/lib";
import type { QueryResponse } from "@/types";

type RetrievalMode = "hybrid" | "dense" | "sparse";
const modes: { label: string; value: RetrievalMode }[] = [{ label: "Hybrid", value: "hybrid" }, { label: "Dense", value: "dense" }, { label: "Sparse", value: "sparse" }];
const suggestions = ["混合检索和单路检索有什么区别？", "API 的错误信封包含哪些字段？", "文档处理任务有哪些阶段？"];

export function QueryPlayground() {
  const loadCollections = useCallback((signal: AbortSignal) => apiClient.listCollections({ limit: 100 }, signal), []);
  const collections = useApiResource(loadCollections);
  const [collectionId, setCollectionId] = useState("");
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [mode, setMode] = useState<RetrievalMode>("hybrid");
  const [topK, setTopK] = useState(5);
  const [rerank, setRerank] = useState(false);
  const [result, setResult] = useState<QueryResponse>();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const selectedCollection = collectionId || collections.data?.items[0]?.id || "";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const trimmed = query.trim(); if (!trimmed || !selectedCollection) return;
    setPending(true); setError(undefined); setResult(undefined); setSubmittedQuery(trimmed);
    try { setResult(await apiClient.queryCollection(selectedCollection, { query: trimmed, top_k: topK, mode, enable_rerank: rerank })); }
    catch (reason) { setError(reason); } finally { setPending(false); }
  }
  const apiError = error instanceof ApiError ? error : undefined;

  return <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_21rem]">
    <div className="min-w-0 space-y-5">
      <form className="glass-surface rounded-xl p-4 sm:p-5" onSubmit={submit}><label className="block text-sm font-semibold" htmlFor="rag-query">向知识库检索</label><div className="mt-3 rounded-lg border border-border bg-surface focus-within:border-primary"><textarea className="min-h-28 w-full resize-none bg-transparent px-4 py-3 text-sm leading-6 outline-none" id="rag-query" maxLength={2048} onChange={(event) => setQuery(event.target.value)} placeholder="输入问题，检索相关文档片段…" value={query} /><div className="flex items-center justify-between border-t border-border px-3 py-2.5"><span className="text-[0.6875rem] text-muted-foreground">{query.length} / 2048</span><button className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-45" disabled={pending || !query.trim() || !selectedCollection} type="submit"><Send className="size-3.5" />{pending ? "检索中…" : "运行检索"}</button></div></div>{!submittedQuery ? <div className="mt-4 flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">试着问：</span>{suggestions.map((item) => <button className="rounded-full border border-border bg-surface-muted/60 px-3 py-1.5 text-xs text-muted-foreground hover:text-primary" key={item} onClick={() => setQuery(item)} type="button">{item}</button>)}</div> : null}</form>

      {pending ? <LoadingState label="正在执行检索" rows={5} /> : error ? <ErrorState {...(apiError ? { code: apiError.code, ...(apiError.requestId ? { requestId: apiError.requestId } : {}) } : {})} description={apiError?.code === "UPSTREAM_ERROR" ? "向量服务暂时不可用。可切换 Sparse 模式重试，或检查 Provider 配置。" : error instanceof Error ? error.message : "查询失败"} onRetry={() => { setError(undefined); setSubmittedQuery(""); }} title="查询失败" /> : result ? <Results query={submittedQuery} result={result} /> : <EmptyState description="选择知识库并输入问题后，这里会展示真实召回片段、分数和诊断信息。" icon={Sparkles} title="探索你的知识库" />}
    </div>

    <aside className="space-y-5 xl:sticky xl:top-[calc(var(--topbar-height)+1.5rem)] xl:self-start"><section className="glass-surface rounded-xl p-5"><div className="flex items-center gap-2"><Settings2 className="size-4 text-primary" /><h2 className="font-semibold">检索配置</h2></div>{collections.loading ? <div className="mt-5"><LoadingState label="加载知识库" rows={2} /></div> : collections.error ? <div className="mt-5"><ErrorState onRetry={collections.retry} title="知识库加载失败" /></div> : <div className="mt-5 space-y-5"><div><label className="text-xs font-semibold text-muted-foreground" htmlFor="collection">知识库</label><div className="relative mt-2"><Database className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><select className="h-10 w-full appearance-none rounded-md border border-border bg-surface pl-9 pr-9 text-sm" id="collection" onChange={(event) => setCollectionId(event.target.value)} value={selectedCollection}>{collections.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /></div></div><fieldset><legend className="text-xs font-semibold text-muted-foreground">检索模式</legend><div className="mt-2 grid grid-cols-3 rounded-md border border-border bg-surface-muted p-1">{modes.map((item) => <button className={cn("h-8 rounded-sm text-xs font-medium", mode === item.value ? "bg-surface-raised text-primary shadow-sm" : "text-muted-foreground")} key={item.value} onClick={() => setMode(item.value)} type="button">{item.label}</button>)}</div></fieldset><div><div className="flex justify-between"><label className="text-xs font-semibold text-muted-foreground" htmlFor="top-k">Top K</label><span className="font-mono text-xs text-primary">{topK}</span></div><input className="mt-3 w-full accent-primary" id="top-k" max="50" min="1" onChange={(event) => setTopK(Number(event.target.value))} type="range" value={topK} /></div><label className="flex items-center justify-between gap-4 rounded-md border border-border bg-surface p-3"><span><span className="block text-sm font-medium">请求 Rerank</span><span className="block text-[0.6875rem] text-warning">v0.2 后端暂未真实执行</span></span><input checked={rerank} className="size-4 accent-primary" onChange={(event) => setRerank(event.target.checked)} type="checkbox" /></label></div>}</section>{result ? <Diagnostics result={result} /> : null}</aside>
  </div>;
}

function Results({ query, result }: { query: string; result: QueryResponse }) {
  return <section className="glass-surface overflow-hidden rounded-xl" aria-live="polite"><div className="border-b border-border bg-surface-muted/45 p-5"><p className="text-xs font-semibold uppercase text-muted-foreground">本次查询</p><h2 className="mt-1 text-lg font-semibold">{query}</h2></div><div className="p-5 sm:p-6"><div className="flex items-center justify-between gap-3"><div><h3 className="font-semibold">检索上下文</h3><p className="mt-1 text-sm text-muted-foreground">返回 {result.citations?.length ?? 0} 个可核验片段</p></div><StatusBadge label="真实 API" status={result.diagnostics.degraded ? "degraded" : "ok"} /></div><div className="mt-4 rounded-lg border border-info/20 bg-info/5 p-4 text-sm text-muted-foreground"><p className="font-medium text-foreground">MCP Server 与 Agent 职责分离</p><p className="mt-1">这里展示 Server 提供的检索上下文、引用和图片；调用端 Agent 使用这些上下文生成最终回答并决定如何呈现引用。</p></div>{result.citations?.length ? <div className="mt-6 space-y-3">{result.citations.map((citation) => <article className="rounded-lg border border-border bg-surface p-4" key={citation.chunk_id}><div className="flex gap-3"><span className="grid size-7 shrink-0 place-items-center rounded-md bg-primary/10 font-mono text-xs font-bold text-primary">{citation.index}</span><div className="min-w-0 flex-1"><div className="flex flex-wrap gap-3 text-sm"><span className="inline-flex items-center gap-1.5 font-semibold"><FileText className="size-3.5 text-danger" />{citation.document_name}</span>{citation.page ? <span className="text-muted-foreground">第 {citation.page} 页</span> : null}</div><p className="mt-3 text-sm leading-6 text-muted-foreground">{citation.text}</p><div className="mt-4 flex flex-wrap gap-2 font-mono text-[0.6875rem]">{Object.entries(citation.scores ?? {}).filter(([, value]) => value != null).map(([name, value]) => <span className="rounded bg-surface-muted px-2 py-1" key={name}>{name} {Number(value).toFixed(3)}</span>)}</div>{citation.images?.length ? <div className="mt-4 grid gap-3 sm:grid-cols-2">{citation.images.map((item) => <figure className="overflow-hidden rounded-lg border border-border" key={item.id}><Image alt={item.caption ?? "引用图片"} className="h-auto w-full object-contain" height={480} src={apiClient.imageUrl(item.url)} unoptimized width={720} /><figcaption className="flex items-center gap-1.5 p-2 text-xs text-muted-foreground"><ImageIcon className="size-3" />{item.caption ?? "文档图片"}</figcaption></figure>)}</div> : null}</div></div></article>)}</div> : <div className="mt-6"><EmptyState description="当前知识库没有召回匹配片段，可调整问题、模式或知识库后重试。" icon={Search} title="没有检索结果" /></div>}</div></section>;
}

function Diagnostics({ result }: { result: QueryResponse }) {
  const d = result.diagnostics;
  const items = [{ icon: Clock3, label: "总耗时", value: `${Math.round(d.duration_ms)} ms` }, { icon: Layers3, label: "Dense", value: d.dense_count ?? "—" }, { icon: SlidersHorizontal, label: "Sparse", value: d.sparse_count ?? "—" }, { icon: Sparkles, label: "Reranked", value: d.reranked_count ?? "未执行" }];
  return <section className="glass-surface rounded-xl p-5"><div className="flex items-center justify-between"><div className="flex items-center gap-2"><Gauge className="size-4 text-primary" /><h2 className="font-semibold">诊断信息</h2></div><StatusBadge label={d.degraded ? "已降级" : "未降级"} status={d.degraded ? "degraded" : "ok"} /></div>{d.degraded_reasons?.length ? <ul className="mt-4 space-y-1 rounded-md bg-warning/10 p-3 text-xs text-warning">{d.degraded_reasons.map((reason) => <li key={reason}>• {reason}</li>)}</ul> : null}<dl className="mt-5 grid grid-cols-2 gap-3">{items.map(({ icon: Icon, label, value }) => <div className="rounded-md bg-surface-muted/70 p-3" key={label}><dt className="flex items-center gap-1.5 text-[0.6875rem] text-muted-foreground"><Icon className="size-3" />{label}</dt><dd className="mt-1 font-mono text-sm font-semibold">{value}</dd></div>)}</dl>{d.trace_id ? <Link className="mt-4 inline-flex items-center gap-1.5 text-xs font-semibold text-primary" href={`/traces?type=query&id=${d.trace_id}`}>查看完整 Trace<ArrowUpRight className="size-3.5" /></Link> : null}</section>;
}
