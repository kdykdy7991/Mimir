"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Boxes, FileStack, Plus, Search, Sparkles, Trash2, X } from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { useApiResource } from "@/api/use-api-resource";
import { Button, ConfirmDialog, EmptyState, ErrorState, LoadingState } from "@/components";
import type { CollectionDetail } from "@/types";

const EMPTY_COLLECTIONS: CollectionDetail[] = [];

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium" }).format(new Date(value));
}

export function CollectionsView() {
  const load = useCallback((signal: AbortSignal) => apiClient.listCollections({ limit: 100 }, signal), []);
  const { data, error, loading, retry } = useApiResource(load);
  const [query, setQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<CollectionDetail>();
  const [mutationError, setMutationError] = useState<unknown>();
  const [additionalItems, setAdditionalItems] = useState<CollectionDetail[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>();
  const [loadingMore, setLoadingMore] = useState(false);
  const items = useMemo(() => [...(data?.items ?? EMPTY_COLLECTIONS), ...additionalItems], [data, additionalItems]);
  const filtered = useMemo(() => items.filter((item) => item.name.toLowerCase().includes(query.trim().toLowerCase())), [items, query]);
  const apiError = error instanceof ApiError ? error : undefined;

  async function remove() {
    if (!deleteTarget) return;
    try { await apiClient.deleteCollection(deleteTarget.id); setDeleteTarget(undefined); retry(); }
    catch (reason) { setMutationError(reason); setDeleteTarget(undefined); }
  }

  async function loadMore() {
    const cursor = nextCursor === undefined ? data?.page_info.next_cursor : nextCursor;
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await apiClient.listCollections({ cursor, limit: 100 });
      setAdditionalItems((current) => [...current, ...page.items]);
      setNextCursor(page.page_info.next_cursor ?? null);
    } catch (reason) { setMutationError(reason); }
    finally { setLoadingMore(false); }
  }

  return <div className="app-container space-y-6">
    <header className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end"><div><div className="mb-3 flex items-center gap-2"><span className="font-mono text-xs font-semibold tracking-[0.14em] text-primary uppercase">Knowledge base</span><span className="rounded-full border border-success/20 bg-success/10 px-2.5 py-0.5 text-[0.6875rem] text-success">实时 API</span></div><h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">知识库</h1><p className="mt-2 max-w-2xl text-muted-foreground">按业务边界组织文档，为查询指定清晰、可控的检索范围。</p></div><Button onClick={() => setDialogOpen(true)}><Plus aria-hidden="true" className="size-4" />新建知识库</Button></header>
    <section className="glass-surface rounded-xl p-4 sm:p-5"><div className="flex flex-col gap-3 sm:flex-row sm:items-center"><label className="relative min-w-0 flex-1"><span className="sr-only">搜索知识库</span><Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><input className="h-10 w-full rounded-md border border-border bg-surface pl-9 pr-3 text-sm outline-none focus:border-primary" onChange={(event) => setQuery(event.target.value)} placeholder="按名称搜索知识库…" value={query} /></label><p className="text-xs text-muted-foreground">共 {items.length} 个知识库</p></div></section>
    {mutationError ? <ErrorState {...(mutationError instanceof ApiError ? { code: mutationError.code, ...(mutationError.requestId ? { requestId: mutationError.requestId } : {}) } : {})} {...(mutationError instanceof Error ? { description: mutationError.message } : {})} title="操作失败" /> : null}
    {loading ? <LoadingState label="正在加载知识库" rows={5} /> : error ? <ErrorState {...(apiError ? { code: apiError.code, ...(apiError.requestId ? { requestId: apiError.requestId } : {}) } : {})} {...(error instanceof Error ? { description: error.message } : {})} onRetry={retry} title="无法加载知识库" /> : filtered.length === 0 ? <EmptyState action={!query ? <Button onClick={() => setDialogOpen(true)}><Plus className="size-4" />创建第一个知识库</Button> : undefined} description={query ? "没有匹配当前关键词的知识库。" : "创建知识库后即可上传并组织文档。"} icon={Boxes} title={query ? "没有搜索结果" : "还没有知识库"} /> : <><section aria-label="知识库列表" className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{filtered.map((collection, index) => <article className="glass-surface group flex min-h-64 flex-col rounded-xl p-5 transition-all hover:-translate-y-0.5 hover:border-border-strong hover:shadow-lg" key={collection.id}><div className="flex items-start justify-between gap-3"><span className={`grid size-11 place-items-center rounded-lg ${index % 3 === 1 ? "bg-accent/10 text-accent" : index % 3 === 2 ? "bg-success/10 text-success" : "bg-primary/10 text-primary"}`}><Boxes className="size-5" /></span><button aria-label={`删除 ${collection.name}`} className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-danger/10 hover:text-danger" onClick={() => setDeleteTarget(collection)} type="button"><Trash2 className="size-4" /></button></div><h2 className="mt-5 text-lg font-semibold">{collection.name}</h2><p className="mt-2 line-clamp-2 text-sm text-muted-foreground">{collection.description || "暂无描述"}</p><div className="mt-auto flex items-center gap-5 pt-6 text-xs text-muted-foreground"><span className="inline-flex items-center gap-1.5"><FileStack className="size-3.5" />{collection.document_count ?? 0} 文档</span><span className="inline-flex items-center gap-1.5"><Sparkles className="size-3.5" />{(collection.chunk_count ?? 0).toLocaleString()} 片段</span></div><div className="mt-4 flex items-center justify-between border-t border-border pt-4"><span className="text-[0.6875rem] text-muted-foreground">更新于 {formatDate(collection.updated_at)}</span><Link className="inline-flex items-center gap-1 text-xs font-semibold text-primary" href={`/collections/${collection.id}`}>查看详情<ArrowRight className="size-3.5" /></Link></div></article>)}</section>{(nextCursor === undefined ? data?.page_info.next_cursor : nextCursor) ? <div className="flex justify-center"><Button loading={loadingMore} onClick={loadMore} variant="secondary">加载更多</Button></div> : null}</>}
    <CreateCollectionDialog onCreated={() => { setDialogOpen(false); retry(); }} onOpenChange={setDialogOpen} open={dialogOpen} />
    <ConfirmDialog confirmLabel="删除知识库" description="知识库删除后无法恢复。若其中仍有文档，后端可能拒绝本次操作。" destructive onConfirm={remove} onOpenChange={(open) => { if (!open) setDeleteTarget(undefined); }} open={Boolean(deleteTarget)} title={`删除“${deleteTarget?.name ?? ""}”？`} />
  </div>;
}

function CreateCollectionDialog({ onCreated, onOpenChange, open }: { onCreated: () => void; onOpenChange: (open: boolean) => void; open: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  useEffect(() => { const dialog = ref.current; if (!dialog) return; if (open && !dialog.open) dialog.showModal(); if (!open && dialog.open) dialog.close(); }, [open]);
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = new FormData(event.currentTarget); setPending(true); setError(undefined); try { await apiClient.createCollection({ name: String(form.get("name") ?? "").trim(), description: String(form.get("description") ?? "").trim() || null }); onCreated(); } catch (reason) { setError(reason); } finally { setPending(false); } }
  return <dialog className="m-auto w-[min(calc(100%-2rem),32rem)] rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-lg backdrop:bg-foreground/20" ref={ref}><form onSubmit={submit}><div className="flex items-start justify-between border-b border-border p-6"><div><h2 className="text-lg font-semibold">新建知识库</h2><p className="mt-1 text-xs text-muted-foreground">名称必须唯一，创建后即可上传文档。</p></div><button aria-label="关闭" onClick={() => onOpenChange(false)} type="button"><X className="size-4" /></button></div><div className="space-y-4 p-6"><label className="block text-sm font-medium">名称<input autoFocus className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3 outline-none focus:border-primary" maxLength={128} name="name" required /></label><label className="block text-sm font-medium">描述<textarea className="mt-2 min-h-24 w-full resize-y rounded-md border border-border bg-surface p-3 outline-none focus:border-primary" maxLength={1024} name="description" /></label>{error ? <p className="text-xs text-danger">{error instanceof Error ? error.message : "创建失败"}</p> : null}</div><div className="flex justify-end gap-2 border-t border-border bg-surface-muted/50 px-6 py-4"><Button onClick={() => onOpenChange(false)} type="button" variant="ghost">取消</Button><Button loading={pending} type="submit">创建</Button></div></form></dialog>;
}
