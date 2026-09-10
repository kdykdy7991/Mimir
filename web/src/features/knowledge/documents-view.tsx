"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ChevronDown, FileCheck2, FileClock, FileText, Files, LoaderCircle, RotateCcw, Search, UploadCloud, X } from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { useApiResource } from "@/api/use-api-resource";
import { Button, ConfirmDialog, EmptyState, ErrorState, LoadingState, StatusBadge } from "@/components";
import type { CollectionDetail, TaskStatusResponse } from "@/types";
import { DocumentTable, type DocumentRow } from "./document-table";
import { createUploadBatchId } from "./upload-batch-id";
import { calculateBatchProgress } from "./upload-batch-progress";
import { DOCUMENT_ACCEPT, isSupportedDocument, SUPPORTED_FORMAT_LABEL } from "./document-presentation";

type PageInfo = { next_cursor: string | null; has_more: boolean };
type Data = { collections: CollectionDetail[]; documents: DocumentRow[]; pageInfo?: PageInfo; total?: number };
type LoadArgs = {
  collection?: CollectionDetail;
  collectionId?: string;
  signal: AbortSignal;
  cursor?: string;
};
type UploadState = "queued" | "uploading" | "accepted" | "skipped" | "rejected" | "failed";
type UploadItem = { file: File; state: UploadState; error?: string };
type UploadBatchFile = { id: string; filename: string; state: UploadState; error?: string; durationMs?: number; serverDurationMs?: number; requestId?: string };
type UploadBatch = { id: string; total: number; settledWithoutTask: number; elapsedMs: number; files: UploadBatchFile[]; tasks: { id: string; filename: string }[] };

const MAX_FILE_SIZE = 30 * 1024 * 1024;
const MAX_BATCH_SIZE = 2 * 1024 * 1024 * 1024;
const MAX_BATCH_FILES = 100;
const DOCUMENT_PAGE_SIZE = 20;
const UPLOAD_TERMINAL_STATES = new Set<UploadState>(["accepted", "skipped", "rejected", "failed"]);
const TASK_TERMINAL_STATES = new Set(["succeeded", "failed", "cancelled", "skipped"]);

function isUploadBatchComplete(batch: UploadBatch) {
  return batch.files.length === batch.total && batch.files.every((item) => UPLOAD_TERMINAL_STATES.has(item.state));
}

function nowMs() {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function formatDuration(value: number) {
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`;
}

function sizeBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function uploadTimingLabel(item: UploadBatchFile) {
  if (item.durationMs === undefined) return undefined;
  const request = item.requestId ? ` · ID ${item.requestId}` : "";
  if (item.serverDurationMs === undefined) return `往返 ${formatDuration(item.durationMs)}${request}`;
  const outsideServer = Math.max(0, item.durationMs - item.serverDurationMs);
  return `往返 ${formatDuration(item.durationMs)} · 服务端 ${formatDuration(item.serverDurationMs)} · 网络/Next/代理 ${formatDuration(outsideServer)}${request}`;
}

async function loadData({ collection, collectionId, signal, cursor }: LoadArgs): Promise<Data> {
  // Single-collection mode. In the real collection-detail page the parent
  // already rendered ``collection``, so we skip the redundant getCollection.
  // When only ``collectionId`` is given (other callers / tests) we still
  // fetch the collection once for its name + document total.
  if (collectionId || collection) {
    const detail = collection ?? (await apiClient.getCollection(collectionId as string, signal));
    const page = await apiClient.listDocuments(
      detail.id,
      { ...(cursor ? { cursor } : {}), limit: DOCUMENT_PAGE_SIZE },
      signal,
    );
    const documents = page.items.map((document) => ({
      ...document, collectionName: detail.name,
    }));
    return {
      collections: [detail],
      documents,
      pageInfo: page.page_info,
      total: detail.document_count ?? documents.length,
    };
  }

  // Cross-collection ("全部文档") page: the server pages across every
  // collection, so we only fetch one page of documents instead of every
  // document in the corpus. Collections are still listed once (their stats
  // are now aggregated cheaply server-side) for the name map + upload target.
  const collections: CollectionDetail[] = [];
  let collectionCursor: string | null | undefined;
  do {
    const page = await apiClient.listCollections(
      { ...(collectionCursor ? { cursor: collectionCursor } : {}), limit: 100 },
      signal,
    );
    collections.push(...page.items);
    collectionCursor = page.page_info.next_cursor;
  } while (collectionCursor);
  const nameById = new Map(collections.map((item) => [item.id, item.name]));
  const page = await apiClient.listAllDocuments(
    { ...(cursor ? { cursor } : {}), limit: DOCUMENT_PAGE_SIZE },
    signal,
  );
  const documents = page.items.map((document) => ({
    ...document,
    collectionName: nameById.get(document.collection_id) ?? document.collection_id,
  }));
  return {
    collections,
    documents,
    pageInfo: page.page_info,
    total: collections.reduce((sum, item) => sum + (item.document_count ?? 0), 0),
  };
}

export function DocumentsView({ collection, collectionId, embedded = false }: {
  collection?: CollectionDetail; collectionId?: string; embedded?: boolean;
}) {
  const [pagination, setPagination] = useState<{ collectionId?: string; cursors: (string | undefined)[] }>({
    ...(collectionId ? { collectionId } : {}),
    cursors: [undefined],
  });
  const pageCursors = pagination.collectionId === collectionId ? pagination.cursors : [undefined];
  const currentCursor = pageCursors[pageCursors.length - 1];
  const currentPage = pageCursors.length;
  const load = useCallback(
    (signal: AbortSignal) => {
      const args: LoadArgs = { signal };
      if (collection) args.collection = collection;
      if (collectionId) args.collectionId = collectionId;
      if (currentCursor !== undefined) args.cursor = currentCursor;
      return loadData(args);
    },
    [collection, collectionId, currentCursor],
  );
  const { data, error, loading, retry } = useApiResource(load);
  const [query, setQuery] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DocumentRow>();
  const [uploadBatches, setUploadBatches] = useState<UploadBatch[]>([]);
  const [actionError, setActionError] = useState<unknown>();
  const filtered = useMemo(() => (data?.documents ?? []).filter((item) => item.filename.toLowerCase().includes(query.trim().toLowerCase())), [data, query]);
  const totalDocuments = data?.total;
  const totalPages = totalDocuments === undefined ? undefined : Math.max(1, Math.ceil(totalDocuments / DOCUMENT_PAGE_SIZE));

  function previousPage() {
    setQuery("");
    retry();
    setPagination((current) => ({
      ...(collectionId ? { collectionId } : {}),
      cursors: current.collectionId === collectionId && current.cursors.length > 1 ? current.cursors.slice(0, -1) : [undefined],
    }));
  }

  function nextPage() {
    const nextCursor = data?.pageInfo?.next_cursor;
    if (!nextCursor) return;
    setQuery("");
    retry();
    setPagination((current) => ({
      ...(collectionId ? { collectionId } : {}),
      cursors: current.collectionId === collectionId ? [...current.cursors, nextCursor] : [undefined, nextCursor],
    }));
  }

  async function remove() {
    if (!deleteTarget) return;
    try { await apiClient.deleteDocument(deleteTarget.id); setDeleteTarget(undefined); retry(); }
    catch (reason) { setActionError(reason); setDeleteTarget(undefined); }
  }

  const ready = data?.documents.filter((item) => item.status === "ready").length ?? 0;
  const apiError = error instanceof ApiError ? error : undefined;
  return <div className={embedded ? "space-y-5" : "app-container space-y-6"}>
    {!embedded ? <header className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end"><div><div className="mb-3 flex items-center gap-2"><span className="font-mono text-xs font-semibold tracking-[0.14em] text-primary uppercase">Document center</span><span className="rounded-full border border-success/20 bg-success/10 px-2.5 py-0.5 text-[0.6875rem] text-success">实时 API</span></div><h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">全部文档</h1><p className="mt-2 text-muted-foreground">跨知识库查看处理进度、索引状态和文档规模。</p></div><Button disabled={!data?.collections.length} onClick={() => setUploadOpen(true)}><UploadCloud className="size-4" />上传文件</Button></header> : <div className="flex flex-wrap items-end justify-between gap-4"><div><h2 className="text-lg font-semibold">文档</h2><p className="mt-1 text-sm text-muted-foreground">上传后系统会自动选择解析路径并建立索引。</p></div><Button onClick={() => setUploadOpen(true)}><UploadCloud className="size-4" />上传文件</Button></div>}
    {!embedded && data ? <section className="grid gap-4 sm:grid-cols-3"><Metric icon={Files} label="文档总数" value={data.total ?? data.documents.length} /><Metric icon={FileCheck2} label="当前页就绪" value={ready} tone="success" /><Metric icon={FileClock} label="当前页处理中或异常" value={data.documents.length - ready} tone="warning" /></section> : null}
    {uploadBatches.map((batch, index) => <div className="space-y-3" key={batch.id}><UploadProgressCard batch={batch} {...(index === uploadBatches.length - 1 ? { onRetry: () => setUploadOpen(true) } : {})} />{isUploadBatchComplete(batch) && batch.tasks.length ? <BatchProgressCard batch={batch} onError={setActionError} onSucceeded={retry} /> : null}</div>)}
    {actionError ? <ErrorState {...(actionError instanceof ApiError ? { code: actionError.code, ...(actionError.requestId ? { requestId: actionError.requestId } : {}) } : {})} description={actionError instanceof ApiError && actionError.code === "TASK_NOT_FOUND" ? "服务可能已重启，任务状态已丢失。请确认文档状态，必要时重新上传。" : actionError instanceof Error ? actionError.message : "操作没有成功完成。"} title={actionError instanceof ApiError && actionError.code === "TASK_NOT_FOUND" ? "任务状态已丢失" : "操作失败"} /> : null}
    {loading ? <LoadingState label="正在加载文档" rows={6} /> : error ? <ErrorState {...(apiError ? { code: apiError.code, ...(apiError.requestId ? { requestId: apiError.requestId } : {}) } : {})} {...(error instanceof Error ? { description: error.message } : {})} onRetry={retry} title="无法加载文档" /> : <section className="glass-surface rounded-xl p-4 sm:p-6"><label className="relative block"><span className="sr-only">搜索文档</span><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><input className="h-10 w-full rounded-md border border-border bg-surface pl-9 pr-3 text-sm outline-none focus:border-primary" onChange={(event) => setQuery(event.target.value)} placeholder={collectionId ? "搜索当前页文件名…" : "搜索文件名…"} value={query} /></label><div className="mt-5">{filtered.length ? <DocumentTable documents={filtered} onDelete={setDeleteTarget} /> : <EmptyState description={query ? "没有匹配当前关键词的文档。" : "上传 PDF 或 Markdown 后将在这里显示处理状态。"} icon={Files} title={query ? "没有搜索结果" : "还没有文档"} />}</div>{data?.pageInfo ? <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4"><p className="text-xs text-muted-foreground">共 {totalDocuments ?? data.documents.length} 条 · 第 {currentPage}{totalPages ? ` / ${totalPages}` : ""} 页 · 每页最多 {DOCUMENT_PAGE_SIZE} 条</p><div className="flex items-center gap-2"><Button disabled={currentPage === 1} onClick={previousPage} variant="secondary">上一页</Button><Button disabled={!data.pageInfo.has_more || !data.pageInfo.next_cursor} onClick={nextPage} variant="secondary">下一页</Button></div></div> : null}</section>}
    {data ? <UploadDialog collections={data.collections} {...(collectionId ? { fixedCollectionId: collectionId } : {})} onOpenChange={setUploadOpen} onUploaded={(batch) => { setActionError(undefined); setUploadBatches((current) => [...current.filter((item) => item.id !== batch.id), batch]); }} open={uploadOpen} /> : null}
    <ConfirmDialog confirmLabel="删除文档" description="删除会协调清理向量、稀疏索引、图片与完整性记录，且无法恢复。" destructive onConfirm={remove} onOpenChange={(open) => { if (!open) setDeleteTarget(undefined); }} open={Boolean(deleteTarget)} title={`删除“${deleteTarget?.filename ?? ""}”？`} />
  </div>;
}

function Metric({ icon: Icon, label, tone = "primary", value }: { icon: typeof Files; label: string; tone?: string; value: number }) {
  const toneClass = tone === "success" ? "bg-success/10 text-success" : tone === "warning" ? "bg-warning/10 text-warning" : "bg-primary/10 text-primary";
  return <div className="glass-surface flex items-center gap-4 rounded-lg p-5"><span className={`grid size-10 place-items-center rounded-md ${toneClass}`}><Icon className="size-5" /></span><div><p className="text-2xl font-semibold">{value}</p><p className="text-xs text-muted-foreground">{label}</p></div></div>;
}

function UploadProgressCard({ batch, onRetry }: { batch: UploadBatch; onRetry?: () => void }) {
  const completed = batch.files.filter((item) => UPLOAD_TERMINAL_STATES.has(item.state)).length;
  const failed = batch.files.filter((item) => item.state === "failed" || item.state === "rejected").length;
  const percent = batch.total ? Math.round(completed / batch.total * 100) : 0;
  const status = completed === batch.total ? (failed ? "failed" : "succeeded") : "running";
  const label = completed === batch.total ? (failed ? `${failed} 份失败` : "上传完成") : "上传中";
  const stateLabel: Record<UploadState, string> = {
    queued: "等待上传", uploading: "正在上传", accepted: "已提交处理",
    skipped: "内容重复，已跳过", rejected: "文件被拒绝", failed: "上传失败",
  };
  return <section aria-live="polite" className="rounded-xl border border-border bg-surface-raised p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-semibold">批量文件上传</p><p className="mt-1 text-xs text-muted-foreground">共 {batch.total} 份，已完成 {completed} 份</p></div><StatusBadge label={label} status={status} /></div>
    <div className="mt-4 h-2 overflow-hidden rounded-full bg-surface-muted"><div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${percent}%` }} /></div>
    <div className="mt-2 flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><p className="font-mono text-xs text-muted-foreground">{percent}%</p><p className="text-xs text-muted-foreground">{completed === batch.total ? "总耗时" : "已用时"} {formatDuration(batch.elapsedMs)}</p></div><div className="flex items-center gap-3">{failed && onRetry ? <button className="text-xs font-semibold text-primary" onClick={onRetry} type="button">重试失败项</button> : null}<p className="font-mono text-[0.6875rem] text-muted-foreground">批次 {batch.id}</p></div></div>
    <details className="group mt-4 border-t border-border pt-3"><summary className="flex cursor-pointer list-none items-center justify-between text-xs font-semibold text-primary">查看各文件上传记录<ChevronDown className="size-4 transition-transform group-open:rotate-180" /></summary><div className="mt-3 divide-y divide-border rounded-md border border-border bg-surface">{batch.files.map((item) => <div className="flex items-center justify-between gap-3 px-3 py-3" key={item.id}><div className="min-w-0"><p className="truncate text-sm font-medium">{item.filename}</p><p className={`mt-0.5 truncate text-xs ${item.error ? "text-danger" : "text-muted-foreground"}`}>{item.error ?? stateLabel[item.state]}{uploadTimingLabel(item) ? ` · ${uploadTimingLabel(item)}` : ""}</p></div><StatusBadge label={stateLabel[item.state]} status={item.state === "accepted" || item.state === "skipped" ? "succeeded" : item.state} /></div>)}</div></details>
  </section>;
}

function BatchProgressCard({ batch, onError, onSucceeded }: { batch: UploadBatch; onError: (error: unknown) => void; onSucceeded: () => void }) {
  const [tasks, setTasks] = useState<Record<string, TaskStatusResponse>>({});
  const refreshed = useRef(false);
  useEffect(() => {
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout>;
    let delay = 300;
    const known: Record<string, TaskStatusResponse> = {};

    async function poll() {
      const pending = batch.tasks.filter((item) => !TASK_TERMINAL_STATES.has(known[item.id]?.status ?? ""));
      if (!pending.length || cancelled) return;
      let cursor = 0;
      async function worker() {
        while (!cancelled && cursor < pending.length) {
          const item = pending[cursor++];
          if (!item) continue;
          try {
            const task = await apiClient.getTask(item.id);
            if (cancelled) return;
            known[item.id] = task;
            setTasks((current) => ({ ...current, [task.id]: task }));
          } catch (reason) {
            if (!cancelled) onError(reason);
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(4, pending.length) }, () => worker()));
      if (cancelled) return;
      const remaining = batch.tasks.some((item) => !TASK_TERMINAL_STATES.has(known[item.id]?.status ?? ""));
      if (remaining) {
        delay = Math.min(Math.round(delay * 1.5), 2000);
        timeout = setTimeout(poll, delay);
      }
    }

    void poll();
    return () => { cancelled = true; clearTimeout(timeout); };
  }, [batch.tasks, onError]);

  const taskPercentages = batch.tasks.map((item) => {
    const task = tasks[item.id];
    return task && TASK_TERMINAL_STATES.has(task.status) ? 100 : task?.progress?.percent ?? 0;
  });
  const { completed, percent: overallPercent } = calculateBatchProgress(
    batch.total, batch.settledWithoutTask, taskPercentages,
  );
  useEffect(() => {
    if (completed === batch.total && !refreshed.current) {
      refreshed.current = true;
      onSucceeded();
    }
  }, [batch.total, completed, onSucceeded]);
  return <section aria-live="polite" className="rounded-xl border border-primary/20 bg-primary/5 p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-semibold">批量文档处理</p><p className="mt-1 text-xs text-muted-foreground">共 {batch.total} 份，已处理 {completed} 份</p></div><StatusBadge label={completed === batch.total ? "全部完成" : "处理中"} status={completed === batch.total ? "succeeded" : "running"} /></div>
    <div className="mt-4 h-2 overflow-hidden rounded-full bg-surface-muted"><div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${overallPercent}%` }} /></div>
    <div className="mt-2 flex items-center justify-between gap-3"><p className="font-mono text-xs text-muted-foreground">{overallPercent}%</p><p className="font-mono text-[0.6875rem] text-muted-foreground">批次 {batch.id}</p></div>
    <details className="group mt-4 border-t border-primary/15 pt-3"><summary className="flex cursor-pointer list-none items-center justify-between text-xs font-semibold text-primary">查看各文档处理记录<ChevronDown className="size-4 transition-transform group-open:rotate-180" /></summary><div className="mt-3 divide-y divide-border rounded-md border border-border bg-surface">{batch.tasks.map((item) => {
      const task = tasks[item.id]; const percent = task && TASK_TERMINAL_STATES.has(task.status) ? 100 : task?.progress?.percent ?? 0;
      return <div className="grid gap-2 px-3 py-3 sm:grid-cols-[minmax(0,1fr)_7rem_6rem_auto] sm:items-center" key={item.id}><div className="min-w-0"><p className="truncate text-sm font-medium">{item.filename}</p><p className="mt-0.5 truncate text-xs text-muted-foreground">{task?.progress?.message ?? (task?.status === "succeeded" ? "处理完成" : task?.error?.message ?? "等待任务状态")}</p></div><p className="font-mono text-xs text-muted-foreground">{percent}% · {task?.progress?.stage ?? task?.status ?? "pending"}</p><StatusBadge status={task?.status ?? "pending"} /><Link className="text-xs font-semibold text-primary" href={`/traces?type=ingestion&id=${item.id}`}>查看 Trace</Link></div>;
    })}</div></details>
  </section>;
}

function UploadDialog({ collections, fixedCollectionId, onOpenChange, onUploaded, open }: { collections: CollectionDetail[]; fixedCollectionId?: string; onOpenChange: (open: boolean) => void; onUploaded: (batch: UploadBatch) => void; open: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const [batchId, setBatchId] = useState<string>();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open) {
      if (dialog.open) dialog.close();
    }
  }, [open]);

  function selectFiles(files: FileList | readonly File[] | null) {
    const selected = Array.from(files ?? []);
    if (selected.length > MAX_BATCH_FILES) { setError(`每批最多上传 ${MAX_BATCH_FILES} 个文件`); setItems([]); return; }
    const invalid = selected.find((file) => !isSupportedDocument(file.name));
    if (invalid) { setError(`${invalid.name}：暂不支持该文件格式`); setItems([]); return; }
    const oversized = selected.find((file) => file.size > MAX_FILE_SIZE);
    if (oversized) { setError(`${oversized.name}：文件不能超过 30 MB`); setItems([]); return; }
    const totalSize = selected.reduce((sum, file) => sum + file.size, 0);
    if (totalSize > MAX_BATCH_SIZE) {
      setError("本批文件总大小不能超过 2 GB");
      setItems([]);
      return;
    }
    setError(undefined);
    setItems(selected.map((file) => ({ file, state: "queued" })));
  }

  function removeSelectedFile(file: File) {
    setItems((current) => current.filter((item) => item.file !== file));
  }

  async function upload(targets: UploadItem[], collectionId: string) {
    if (!targets.length || !collectionId) return;
    setPending(true); setError(undefined);
    const clientBatchId = createUploadBatchId();
    const tasks: UploadBatch["tasks"] = [];
    const batchStartedAt = nowMs();
    const fileIds = new Map(targets.map((target, index) => [target.file, `${clientBatchId}-${index + 1}`]));
    let files: UploadBatchFile[] = targets.map((target) => ({ id: fileIds.get(target.file)!, filename: target.file.name, state: "queued" }));
    let settledWithoutTask = 0;
    let failed = 0;
    setBatchId(clientBatchId);

    function publishBatch() {
      onUploaded({ id: clientBatchId, total: targets.length, settledWithoutTask, elapsedMs: nowMs() - batchStartedAt, files: [...files], tasks: [...tasks] });
    }
    function updateFile(target: UploadItem, state: UploadState, error?: string, timing?: { durationMs: number; serverDurationMs?: number; requestId?: string }) {
      const id = fileIds.get(target.file);
      files = files.map((item) => item.id === id ? { id: item.id, filename: item.filename, state, ...(error ? { error } : {}), ...(timing ? timing : {}) } : item);
      publishBatch();
    }
    publishBatch();

    function messageFor(reason: unknown) {
      return reason instanceof ApiError
        ? reason.status === 413
          ? "上传内容超过服务允许的大小，请减少文件数量或文件大小。"
          : reason.status >= 500
            ? "上传代理或服务端处理失败，请确认服务已重启并稍后重试。"
            : `${reason.code}：${reason.message}`
        : reason instanceof Error
          ? reason.message
          : "文件上传失败";
    }

    async function submitOne(target: UploadItem) {
      const requestStartedAt = nowMs();
      updateFile(target, "uploading");
      setItems((current) => current.map((item) => item.file === target.file ? { file: item.file, state: "uploading" } : item));
      try {
        // One multipart request per file makes completion observable and
        // prevents one slow request from holding the whole batch open.
        const timed = await apiClient.uploadDocumentsWithTiming(collectionId, [target.file]);
        const response = timed.response;
        const result = response.files[0];
        if (!result) throw new Error("服务端响应缺少该文件的结果");
        const rejectedMessage = result.error ? `${result.error.code}：${result.error.message}` : "文件被服务端拒绝";
        const nextState = result.status === "accepted" ? "accepted" : result.status === "skipped" ? "skipped" : "rejected";
        updateFile(target, nextState, nextState === "rejected" ? rejectedMessage : undefined, {
          durationMs: timed.durationMs,
          ...(timed.serverDurationMs !== undefined ? { serverDurationMs: timed.serverDurationMs } : {}),
          ...(timed.requestId ? { requestId: timed.requestId } : {}),
        });
        setItems((current) => current.map((item) => item.file === target.file ? { file: item.file, state: nextState, ...(nextState === "rejected" ? { error: rejectedMessage } : {}) } : item));
        if (result.task_id) tasks.push({ id: result.task_id, filename: result.filename });
        else settledWithoutTask += 1;
        publishBatch();
      } catch (reason) {
        failed += 1;
        settledWithoutTask += 1;
        const message = messageFor(reason);
        updateFile(target, "failed", message, { durationMs: nowMs() - requestStartedAt });
        setItems((current) => current.map((item) => item.file === target.file ? { file: item.file, state: "failed", error: message } : item));
      }
    }

    try {
      // Four upload lanes keep the UI moving without flooding the API or
      // the browser connection pool when the user selects all 50 files.
      let next = 0;
      async function worker() {
        while (next < targets.length) {
          const target = targets[next++];
          if (target) await submitOne(target);
        }
      }
      await Promise.all(Array.from({ length: Math.min(4, targets.length) }, () => worker()));
      if (failed) setError(`${failed} 个文件上传失败，可点击下方按钮仅重试失败项。`);
    } finally {
      setPending(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const id = fixedCollectionId ?? String(form.get("collection"));
    void upload(items.filter((item) => item.state === "queued" || item.state === "failed"), id);
    onOpenChange(false);
  }

  const failedCount = items.filter((item) => item.state === "failed").length;
  const finished = items.length > 0 && items.every((item) => ["accepted", "skipped", "rejected"].includes(item.state));
  const fixedCollection = fixedCollectionId ? collections.find((item) => item.id === fixedCollectionId) : undefined;
  return <dialog className="m-auto w-[min(calc(100%-2rem),42rem)] rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-lg backdrop:bg-foreground/20" ref={ref}><form onSubmit={submit}><div className="flex items-start justify-between border-b border-border p-6"><div><h2 className="text-lg font-semibold">批量上传文档</h2><p className="mt-1 text-xs text-muted-foreground">{fixedCollection ? `文档将上传至「${fixedCollection.name}」，并自动完成解析与索引。` : "上传后系统将自动解析内容、表格与图片，无需选择解析方式。"}</p></div><button aria-label="关闭" onClick={() => onOpenChange(false)} type="button"><X className="size-4" /></button></div><div className="space-y-4 p-6">{!fixedCollectionId ? <label className="block text-sm font-medium">知识库<select className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3" name="collection" required>{collections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}<label className="block text-sm font-medium">选择文件<span className="mt-2 flex min-h-32 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-primary/35 bg-primary/[0.03] p-5 text-center" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); if (!pending) selectFiles(event.dataTransfer.files); }}><UploadCloud className="size-7 text-primary" /><span className="mt-3 text-sm font-semibold">拖拽文件到这里，或点击选择文件</span><span className="mt-2 max-w-xl text-xs leading-5 text-muted-foreground">支持 {SUPPORTED_FORMAT_LABEL}</span><span className="mt-2 text-[0.6875rem] text-muted-foreground">单文件最大 30 MB · 每批最多 100 个 · 整批最大 2 GB</span></span><input accept={DOCUMENT_ACCEPT} className="sr-only" disabled={pending} multiple onChange={(event) => selectFiles(event.target.files)} required={!items.length} type="file" /></label>{batchId ? <p className="font-mono text-[0.6875rem] text-muted-foreground">批次：{batchId}</p> : null}{items.length ? <><p className="text-sm font-semibold">已选择 {items.length} 个文件</p><ul aria-label="待上传文件" className="max-h-64 divide-y divide-border overflow-y-auto rounded-lg border border-border">{items.map((item) => <li className="flex items-center gap-3 px-4 py-3" key={`${item.file.name}-${item.file.size}-${item.file.lastModified}`}><FileText className="size-4 shrink-0 text-primary" /><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{item.file.name}</p><p className={`text-xs ${item.state === "failed" || item.state === "rejected" ? "text-danger" : "text-muted-foreground"}`}>{item.error ?? (item.state === "queued" ? "等待上传" : item.state === "uploading" ? "正在上传…" : item.state === "accepted" ? "已提交处理" : item.state === "skipped" ? "内容重复，已跳过" : "文件被拒绝")}</p></div><span className="text-xs text-muted-foreground">{sizeBytes(item.file.size)}</span>{item.state === "queued" && !pending ? <button aria-label={`移除 ${item.file.name}`} className="rounded p-1 text-muted-foreground hover:bg-surface-muted hover:text-foreground" onClick={() => removeSelectedFile(item.file)} type="button"><X className="size-4" /></button> : item.state === "uploading" ? <LoaderCircle className="size-4 animate-spin text-primary" /> : item.state === "accepted" || item.state === "skipped" ? <CheckCircle2 className="size-4 text-success" /> : item.state === "failed" ? <RotateCcw className="size-4 text-danger" /> : item.state === "rejected" ? <X className="size-4 text-danger" /> : null}</li>)}</ul></> : null}<div className="rounded-md border border-info/20 bg-info/5 px-4 py-3 text-xs text-muted-foreground">系统会自动判断是否需要视觉增强；所有解析均在本地完成。</div>{error ? <p className="text-xs text-danger">{error}</p> : null}</div><div className="flex items-center justify-between gap-3 border-t border-border bg-surface-muted/50 px-6 py-4"><p className="text-xs text-muted-foreground">{items.length ? `${items.length} 个文件 · 共 ${sizeBytes(items.reduce((sum, item) => sum + item.file.size, 0))}` : "尚未选择文件"}</p><div className="flex gap-2"><Button onClick={() => onOpenChange(false)} type="button" variant="ghost">{finished ? "完成" : "取消"}</Button><Button disabled={!items.length || finished} loading={pending} type="submit">{failedCount ? `重试上传（${failedCount}）` : "上传并处理"}</Button></div></div></form></dialog>;
}
