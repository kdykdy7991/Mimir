"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { ArrowLeft, ChevronRight, FileStack, Pencil, Sparkles, UploadCloud } from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { useApiResource } from "@/api/use-api-resource";
import { Button, ErrorState, LoadingState } from "@/components";
import { DocumentsView } from "./documents-view";
import { EditCollectionDialog } from "./edit-collection-dialog";

export function CollectionDetailView({ id }: { id: string }) {
  const load = useCallback((signal: AbortSignal) => apiClient.getCollection(id, signal), [id]);
  const { data, error, loading, retry } = useApiResource(load);
  const [editOpen, setEditOpen] = useState(false);
  if (loading) return <div className="app-container"><LoadingState label="正在加载知识库详情" rows={6} /></div>;
  if (error || !data) {
    const apiError = error instanceof ApiError ? error : undefined;
    return <div className="app-container"><ErrorState {...(apiError ? { code: apiError.code, ...(apiError.requestId ? { requestId: apiError.requestId } : {}) } : {})} {...(error instanceof Error ? { description: error.message } : {})} onRetry={retry} title="无法加载知识库" /></div>;
  }
  return <div className="app-container space-y-6">
    <nav aria-label="面包屑" className="flex items-center gap-1.5 text-xs text-muted-foreground"><Link className="hover:text-foreground" href="/collections">知识库</Link><ChevronRight className="size-3" /><span className="truncate text-foreground">{data.name}</span></nav>
    <div className="flex justify-end"><Button onClick={() => setEditOpen(true)} variant="secondary"><Pencil className="size-4" />编辑描述</Button></div>
    <header><Link className="mb-4 inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground" href="/collections"><ArrowLeft className="size-3.5" />返回知识库</Link><div className="flex flex-wrap items-center gap-3"><h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">{data.name}</h1><span className="rounded-full border border-success/20 bg-success/10 px-2.5 py-0.5 text-[0.6875rem] text-success">实时 API</span></div><p className="mt-2 max-w-2xl text-muted-foreground">{data.description || "暂无描述"}</p></header>
    <section aria-label="知识库统计" className="grid gap-4 sm:grid-cols-3"><div className="glass-surface rounded-lg p-5"><FileStack className="size-5 text-primary" /><p className="mt-4 text-2xl font-semibold">{data.document_count ?? 0}</p><p className="mt-1 text-xs text-muted-foreground">文档总数</p></div><div className="glass-surface rounded-lg p-5"><Sparkles className="size-5 text-accent" /><p className="mt-4 text-2xl font-semibold">{(data.chunk_count ?? 0).toLocaleString()}</p><p className="mt-1 text-xs text-muted-foreground">可检索片段</p></div><div className="glass-surface rounded-lg p-5"><UploadCloud className="size-5 text-success" /><p className="mt-4 text-sm font-semibold">{new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(data.updated_at))}</p><p className="mt-1 text-xs text-muted-foreground">最后更新</p></div></section>
    <DocumentsView collectionId={id} collection={data} embedded />
    <EditCollectionDialog collection={data} onOpenChange={setEditOpen} onUpdated={() => { setEditOpen(false); retry(); }} open={editOpen} />
  </div>;
}
