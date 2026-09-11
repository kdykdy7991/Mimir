"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Search, Table2, X } from "lucide-react";
import { apiClient } from "@/api";
import { Button, EmptyState, ErrorState, LoadingState } from "@/components";
import type { DocumentChunkDetail, DocumentChunkListParams } from "@/types";
import { ChunkDetailDrawer } from "./chunk-detail-drawer";

const PAGE_SIZES = [20, 50, 100] as const;
type ContentTypeFilter = "" | "text" | "table" | "image_ocr" | "image_caption";

export function DocumentChunksPanel({ documentId, onShowSource }: { documentId: string; onShowSource: (chunk: DocumentChunkDetail) => void }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<(typeof PAGE_SIZES)[number]>(20);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [composing, setComposing] = useState(false);
  const [contentType, setContentType] = useState<ContentTypeFilter>("");
  const [pageNumber, setPageNumber] = useState("");
  const [data, setData] = useState<Awaited<ReturnType<typeof apiClient.listDocumentChunks>>>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string>();
  const [selected, setSelected] = useState<DocumentChunkDetail>();
  const [detailError, setDetailError] = useState<unknown>();
  const [detailLoading, setDetailLoading] = useState(false);
  const listRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (composing) return;
    const timer = setTimeout(() => { setQuery(queryInput.trim()); setPage(1); }, 300);
    return () => clearTimeout(timer);
  }, [composing, queryInput]);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError(undefined);
    const params: DocumentChunkListParams = { page, page_size: pageSize };
    if (query) params.q = query;
    if (contentType) params.content_type = contentType;
    const parsedPage = Number(pageNumber);
    if (Number.isSafeInteger(parsedPage) && parsedPage > 0) params.page_number = parsedPage;
    try { setData(await apiClient.listDocumentChunks(documentId, params, signal)); }
    catch (reason) { if (!signal?.aborted) setError(reason); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [contentType, documentId, page, pageNumber, pageSize, query]);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.resolve().then(() => load(controller.signal));
    return () => controller.abort();
  }, [load]);

  const loadDetail = useCallback(async (chunkId: string) => {
    setSelectedId(chunkId); setSelected(undefined); setDetailError(undefined); setDetailLoading(true);
    try { setSelected(await apiClient.getDocumentChunk(documentId, chunkId)); }
    catch (reason) { setDetailError(reason); }
    finally { setDetailLoading(false); }
  }, [documentId]);

  function clearFilters() { setQueryInput(""); setQuery(""); setContentType(""); setPageNumber(""); setPage(1); }
  const filtered = Boolean(query || contentType || pageNumber);
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize));

  return <section className="glass-surface rounded-xl p-5" ref={listRef}><div className="flex flex-wrap items-end justify-between gap-4"><div><div className="flex items-center gap-2"><Table2 className="size-4 text-primary" /><h2 className="text-lg font-semibold">文档片段</h2></div><p className="mt-1 text-sm text-muted-foreground">检查实际进入检索索引的完整内容。</p></div>{data ? <span className="text-xs text-muted-foreground">共 {data.total.toLocaleString()} 条</span> : null}</div>
    <div className="mt-4 grid gap-3 md:grid-cols-[minmax(12rem,1fr)_10rem_7rem_auto]"><label className="relative"><span className="sr-only">搜索 Chunk 正文</span><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><input className="h-10 w-full rounded-md border border-border bg-surface pl-9 pr-3 text-sm outline-none focus:border-primary" onChange={(event) => setQueryInput(event.target.value)} onCompositionEnd={(event) => { setComposing(false); setQueryInput(event.currentTarget.value); }} onCompositionStart={() => setComposing(true)} placeholder="搜索片段正文…" value={queryInput} /></label><label><span className="sr-only">内容类型</span><select className="h-10 w-full rounded-md border border-border bg-surface px-3 text-sm" onChange={(event) => { setContentType(event.target.value as typeof contentType); setPage(1); }} value={contentType}><option value="">全部类型</option><option value="text">正文</option><option value="table">表格</option><option value="image_ocr">图片 OCR</option><option value="image_caption">图片描述</option></select></label><label><span className="sr-only">原文页码</span><input className="h-10 w-full rounded-md border border-border bg-surface px-3 text-sm" min={1} onChange={(event) => { setPageNumber(event.target.value); setPage(1); }} placeholder="页码" type="number" value={pageNumber} /></label><Button disabled={!filtered} onClick={clearFilters} variant="ghost"><X className="size-4" />清除</Button></div>
    <div className="mt-4">{loading ? <LoadingState label="正在加载文档片段" rows={5} /> : error ? <ErrorState description={error instanceof Error ? error.message : "无法读取文档片段"} onRetry={() => void load()} title="片段加载失败" /> : !data?.items.length ? <EmptyState description={filtered ? "没有符合当前条件的片段。" : "该文档暂时没有可检索片段。"} icon={Table2} title={filtered ? "没有筛选结果" : "暂无片段"} /> : <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full min-w-[46rem] text-left text-sm"><thead className="bg-surface-muted/65 text-xs text-muted-foreground"><tr><th className="px-4 py-3">序号</th><th className="px-4 py-3">内容预览</th><th className="px-4 py-3">章节</th><th className="px-4 py-3">页码</th><th className="px-4 py-3">类型</th><th className="px-4 py-3">字符数</th></tr></thead><tbody className="divide-y divide-border">{data.items.map((item) => <tr className="cursor-pointer hover:bg-primary/[0.035] focus-within:bg-primary/[0.035]" key={item.chunk_id}><td className="px-4 py-3 font-mono text-xs"><button className="focus:outline-none" onClick={() => void loadDetail(item.chunk_id)} type="button">{item.index + 1}</button></td><td className="max-w-sm px-4 py-3"><button className="block w-full truncate text-left font-medium focus:outline-none" onClick={() => void loadDetail(item.chunk_id)} title={item.preview} type="button">{item.preview || "（空片段）"}</button></td><td className="max-w-48 truncate px-4 py-3" title={item.heading ?? undefined}>{item.heading ?? "—"}</td><td className="px-4 py-3">{item.page ?? "—"}</td><td className="px-4 py-3">{item.content_type}</td><td className="px-4 py-3">{item.character_count.toLocaleString()}</td></tr>)}</tbody></table></div>}</div>
    {data?.items.length ? <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4"><div className="flex items-center gap-2 text-xs text-muted-foreground"><span>第 {page} / {totalPages} 页</span><select aria-label="每页数量" className="h-8 rounded border border-border bg-surface px-2" onChange={(event) => { setPageSize(Number(event.target.value) as typeof pageSize); setPage(1); }} value={pageSize}>{PAGE_SIZES.map((value) => <option key={value} value={value}>{value} 条/页</option>)}</select></div><div className="flex gap-2"><Button disabled={page <= 1} onClick={() => { setPage((value) => value - 1); listRef.current?.scrollIntoView({ block: "start" }); }} size="sm" variant="secondary"><ChevronLeft className="size-4" />上一页</Button><Button disabled={!data.has_next} onClick={() => { setPage((value) => value + 1); listRef.current?.scrollIntoView({ block: "start" }); }} size="sm" variant="secondary">下一页<ChevronRight className="size-4" /></Button></div></div> : null}
    <ChunkDetailDrawer chunk={selected} error={detailError} loading={detailLoading} onClose={() => { setSelectedId(undefined); setSelected(undefined); setDetailError(undefined); }} onNavigate={(id) => { void loadDetail(id); }} onRetry={() => { if (selectedId) void loadDetail(selectedId); }} onShowSource={onShowSource} open={Boolean(selectedId)} />
  </section>;
}
