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

type Data = { collections: CollectionDetail[]; documents: DocumentRow[] };
type UploadState = "queued" | "uploading" | "accepted" | "skipped" | "rejected" | "failed";
type UploadItem = { file: File; state: UploadState; error?: string };
type UploadBatch = { id: string; tasks: { id: string; filename: string }[] };

const MAX_FILE_SIZE = 50 * 1024 * 1024;
const MAX_BATCH_SIZE = 100 * 1024 * 1024;
const MAX_BATCH_FILES = 10;

function isSupportedFile(file: File) {
  const name = file.name.toLowerCase();
  return name.endsWith(".pdf") || name.endsWith(".md") || name.endsWith(".markdown");
}

async function loadData(fixedCollectionId: string | undefined, signal: AbortSignal): Promise<Data> {
  const collections: CollectionDetail[] = [];
  if (fixedCollectionId) collections.push(await apiClient.getCollection(fixedCollectionId, signal));
  else {
    let cursor: string | null | undefined;
    do {
      const page = await apiClient.listCollections({ ...(cursor ? { cursor } : {}), limit: 100 }, signal);
      collections.push(...page.items);
      cursor = page.page_info.next_cursor;
    } while (cursor);
  }
  const documents = (await Promise.all(collections.map(async (collection) => {
    const items: DocumentRow[] = []; let cursor: string | null | undefined;
    do {
      const page = await apiClient.listDocuments(collection.id, { ...(cursor ? { cursor } : {}), limit: 100 }, signal);
      items.push(...page.items.map((document) => ({ ...document, collectionName: collection.name })));
      cursor = page.page_info.next_cursor;
    } while (cursor);
    return items;
  }))).flat();
  return { collections, documents };
}

export function DocumentsView({ collectionId, embedded = false }: { collectionId?: string; embedded?: boolean }) {
  const load = useCallback((signal: AbortSignal) => loadData(collectionId, signal), [collectionId]);
  const { data, error, loading, retry } = useApiResource(load);
  const [query, setQuery] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DocumentRow>();
  const [uploadBatches, setUploadBatches] = useState<UploadBatch[]>([]);
  const [actionError, setActionError] = useState<unknown>();
  const filtered = useMemo(() => (data?.documents ?? []).filter((item) => item.filename.toLowerCase().includes(query.trim().toLowerCase())), [data, query]);

  async function remove() {
    if (!deleteTarget) return;
    try { await apiClient.deleteDocument(deleteTarget.id); setDeleteTarget(undefined); retry(); }
    catch (reason) { setActionError(reason); setDeleteTarget(undefined); }
  }

  const ready = data?.documents.filter((item) => item.status === "ready").length ?? 0;
  const apiError = error instanceof ApiError ? error : undefined;
  return <div className={embedded ? "space-y-5" : "app-container space-y-6"}>
    {!embedded ? <header className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end"><div><div className="mb-3 flex items-center gap-2"><span className="font-mono text-xs font-semibold tracking-[0.14em] text-primary uppercase">Document center</span><span className="rounded-full border border-success/20 bg-success/10 px-2.5 py-0.5 text-[0.6875rem] text-success">实时 API</span></div><h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">全部文档</h1><p className="mt-2 text-muted-foreground">跨知识库查看处理进度、索引状态和文档规模。</p></div><Button disabled={!data?.collections.length} onClick={() => setUploadOpen(true)}><UploadCloud className="size-4" />上传文件</Button></header> : <div className="flex flex-wrap items-end justify-between gap-4"><div><h2 className="text-lg font-semibold">文档</h2><p className="mt-1 text-sm text-muted-foreground">批量上传 PDF 或 Markdown 后自动创建处理任务。</p></div><Button onClick={() => setUploadOpen(true)}><UploadCloud className="size-4" />上传文件</Button></div>}
    {!embedded && data ? <section className="grid gap-4 sm:grid-cols-3"><Metric icon={Files} label="文档总数" value={data.documents.length} /><Metric icon={FileCheck2} label="索引就绪" value={ready} tone="success" /><Metric icon={FileClock} label="处理中或异常" value={data.documents.length - ready} tone="warning" /></section> : null}
    {uploadBatches.map((batch) => <BatchProgressCard batch={batch} key={batch.id} onError={setActionError} onSucceeded={retry} />)}
    {actionError ? <ErrorState {...(actionError instanceof ApiError ? { code: actionError.code, ...(actionError.requestId ? { requestId: actionError.requestId } : {}) } : {})} description={actionError instanceof ApiError && actionError.code === "TASK_NOT_FOUND" ? "服务可能已重启，任务状态已丢失。请确认文档状态，必要时重新上传。" : actionError instanceof Error ? actionError.message : "操作没有成功完成。"} title={actionError instanceof ApiError && actionError.code === "TASK_NOT_FOUND" ? "任务状态已丢失" : "操作失败"} /> : null}
    {loading ? <LoadingState label="正在加载文档" rows={6} /> : error ? <ErrorState {...(apiError ? { code: apiError.code, ...(apiError.requestId ? { requestId: apiError.requestId } : {}) } : {})} {...(error instanceof Error ? { description: error.message } : {})} onRetry={retry} title="无法加载文档" /> : <section className="glass-surface rounded-xl p-4 sm:p-6"><label className="relative block"><span className="sr-only">搜索文档</span><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><input className="h-10 w-full rounded-md border border-border bg-surface pl-9 pr-3 text-sm outline-none focus:border-primary" onChange={(event) => setQuery(event.target.value)} placeholder="搜索文件名…" value={query} /></label><div className="mt-5">{filtered.length ? <DocumentTable documents={filtered} onDelete={setDeleteTarget} /> : <EmptyState description={query ? "没有匹配当前关键词的文档。" : "上传 PDF 或 Markdown 后将在这里显示处理状态。"} icon={Files} title={query ? "没有搜索结果" : "还没有文档"} />}</div></section>}
    {uploadOpen ? <UploadDialog collections={data?.collections ?? []} {...(collectionId ? { fixedCollectionId: collectionId } : {})} onOpenChange={setUploadOpen} onUploaded={(batch) => { setActionError(undefined); setUploadBatches((current) => [...current.filter((item) => item.id !== batch.id), batch]); retry(); }} open /> : null}
    <ConfirmDialog confirmLabel="删除文档" description="删除会协调清理向量、稀疏索引、图片与完整性记录，且无法恢复。" destructive onConfirm={remove} onOpenChange={(open) => { if (!open) setDeleteTarget(undefined); }} open={Boolean(deleteTarget)} title={`删除“${deleteTarget?.filename ?? ""}”？`} />
  </div>;
}

function Metric({ icon: Icon, label, tone = "primary", value }: { icon: typeof Files; label: string; tone?: string; value: number }) {
  const toneClass = tone === "success" ? "bg-success/10 text-success" : tone === "warning" ? "bg-warning/10 text-warning" : "bg-primary/10 text-primary";
  return <div className="glass-surface flex items-center gap-4 rounded-lg p-5"><span className={`grid size-10 place-items-center rounded-md ${toneClass}`}><Icon className="size-5" /></span><div><p className="text-2xl font-semibold">{value}</p><p className="text-xs text-muted-foreground">{label}</p></div></div>;
}

function TaskPoller({ onError, onSucceeded, onUpdate, taskId }: { onError: (error: unknown) => void; onSucceeded: () => void; onUpdate: (task: TaskStatusResponse) => void; taskId: string }) {
  const [task, setTask] = useState<TaskStatusResponse>();
  useEffect(() => {
    let cancelled = false; let timeout: ReturnType<typeof setTimeout>; let delay = 1500;
    async function poll() {
      try {
        const next = await apiClient.getTask(taskId);
        if (cancelled) return;
        setTask(next);
        onUpdate(next);
        if (next.status === "succeeded") { onSucceeded(); return; }
        if (["failed", "cancelled"].includes(next.status)) return;
        delay = Math.min(Math.round(delay * 1.5), 5000);
        timeout = setTimeout(poll, delay);
      } catch (reason) { if (!cancelled) onError(reason); }
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timeout); };
  }, [onError, onSucceeded, onUpdate, taskId]);
  return task ? null : null;
}

function BatchProgressCard({ batch, onError, onSucceeded }: { batch: UploadBatch; onError: (error: unknown) => void; onSucceeded: () => void }) {
  const [tasks, setTasks] = useState<Record<string, TaskStatusResponse>>({});
  const onUpdate = useCallback((task: TaskStatusResponse) => setTasks((current) => ({ ...current, [task.id]: task })), []);
  const terminal = new Set(["succeeded", "failed", "cancelled", "skipped"]);
  const completed = batch.tasks.filter((item) => terminal.has(tasks[item.id]?.status ?? "")).length;
  const overallPercent = batch.tasks.length ? Math.round(batch.tasks.reduce((sum, item) => {
    const task = tasks[item.id];
    return sum + (task && terminal.has(task.status) ? 100 : task?.progress?.percent ?? 0);
  }, 0) / batch.tasks.length) : 0;
  return <section aria-live="polite" className="rounded-xl border border-primary/20 bg-primary/5 p-5">
    {batch.tasks.map((item) => <TaskPoller key={item.id} onError={onError} onSucceeded={onSucceeded} onUpdate={onUpdate} taskId={item.id} />)}
    <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-semibold">批量文档处理</p><p className="mt-1 text-xs text-muted-foreground">共 {batch.tasks.length} 份，已处理 {completed} 份</p></div><StatusBadge label={completed === batch.tasks.length ? "全部完成" : "处理中"} status={completed === batch.tasks.length ? "succeeded" : "running"} /></div>
    <div className="mt-4 h-2 overflow-hidden rounded-full bg-surface-muted"><div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${overallPercent}%` }} /></div>
    <div className="mt-2 flex items-center justify-between gap-3"><p className="font-mono text-xs text-muted-foreground">{overallPercent}%</p><p className="font-mono text-[0.6875rem] text-muted-foreground">批次 {batch.id}</p></div>
    <details className="group mt-4 border-t border-primary/15 pt-3"><summary className="flex cursor-pointer list-none items-center justify-between text-xs font-semibold text-primary">查看各文档处理记录<ChevronDown className="size-4 transition-transform group-open:rotate-180" /></summary><div className="mt-3 divide-y divide-border rounded-md border border-border bg-surface">{batch.tasks.map((item) => {
      const task = tasks[item.id]; const percent = task && terminal.has(task.status) ? 100 : task?.progress?.percent ?? 0;
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

  function selectFiles(files: FileList | null) {
    const selected = Array.from(files ?? []);
    if (selected.length > MAX_BATCH_FILES) { setError(`每批最多上传 ${MAX_BATCH_FILES} 个文件`); setItems([]); return; }
    const invalid = selected.find((file) => !isSupportedFile(file));
    if (invalid) { setError(`${invalid.name}：仅支持 PDF、MD 和 Markdown 文件`); setItems([]); return; }
    const oversized = selected.find((file) => file.size > MAX_FILE_SIZE);
    if (oversized) { setError(`${oversized.name}：文件不能超过 50 MB`); setItems([]); return; }
    const totalSize = selected.reduce((sum, file) => sum + file.size, 0);
    if (totalSize > MAX_BATCH_SIZE) {
      setError("本批文件总大小不能超过 100 MB");
      setItems([]);
      return;
    }
    setError(undefined);
    setItems(selected.map((file) => ({ file, state: "queued" })));
  }

  async function upload(targets: UploadItem[], collectionId: string) {
    if (!targets.length || !collectionId) return;
    setPending(true); setError(undefined);
    const targetFiles = new Set(targets.map((item) => item.file));
    setItems((current) => current.map((item) => targetFiles.has(item.file) ? { file: item.file, state: "uploading" } : item));
    try {
      const response = await apiClient.uploadDocuments(collectionId, targets.map((item) => item.file));
      setBatchId(response.batch_id);
      const tasks = response.files.flatMap((result) => result.task_id ? [{ id: result.task_id, filename: result.filename }] : []);
      setItems((current) => current.map((item) => {
        const index = targets.findIndex((target) => target.file === item.file);
        if (index < 0) return item;
        const result = response.files[index];
        if (!result) return { ...item, state: "failed", error: "批量响应缺少该文件的结果" };
        if (result.status === "accepted") return { file: item.file, state: "accepted" };
        if (result.status === "skipped") return { file: item.file, state: "skipped" };
        return { file: item.file, state: "rejected", error: result.error ? `${result.error.code}：${result.error.message}` : "文件被服务端拒绝" };
      }));
      if (tasks.length) onUploaded({ id: response.batch_id, tasks });
    } catch (reason) {
      const message = reason instanceof ApiError
        ? reason.status === 413
          ? "上传内容超过服务允许的大小，请减少文件数量或文件大小。"
          : reason.status >= 500
            ? "上传代理或服务端处理失败，请确认服务已重启并稍后重试。"
            : `${reason.code}：${reason.message}`
        : reason instanceof Error
          ? reason.message
          : "批量上传失败";
      setError(message);
      setItems((current) => current.map((item) => targetFiles.has(item.file) ? { file: item.file, state: "failed", error: message } : item));
    } finally {
      setPending(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const id = fixedCollectionId ?? String(form.get("collection"));
    await upload(items.filter((item) => item.state === "queued" || item.state === "failed"), id);
  }

  const failedCount = items.filter((item) => item.state === "failed").length;
  const finished = items.length > 0 && items.every((item) => ["accepted", "skipped", "rejected"].includes(item.state));
  return <dialog className="m-auto w-[min(calc(100%-2rem),38rem)] rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-lg backdrop:bg-foreground/20" ref={ref}><form onSubmit={submit}><div className="flex items-start justify-between border-b border-border p-6"><div><h2 className="text-lg font-semibold">批量上传文档</h2><p className="mt-1 text-xs text-muted-foreground">支持 PDF、MD、Markdown；每批最多 10 个，单文件最大 50 MB。</p></div><button aria-label="关闭" onClick={() => onOpenChange(false)} type="button"><X className="size-4" /></button></div><div className="space-y-4 p-6">{!fixedCollectionId ? <label className="block text-sm font-medium">知识库<select className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3" name="collection" required>{collections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}<label className="block text-sm font-medium">选择文件<input accept="application/pdf,text/markdown,.pdf,.md,.markdown" className="mt-2 block w-full rounded-md border border-dashed border-border p-4 text-sm" disabled={pending} multiple onChange={(event) => selectFiles(event.target.files)} required={!items.length} type="file" /></label>{batchId ? <p className="font-mono text-[0.6875rem] text-muted-foreground">批次：{batchId}</p> : null}{items.length ? <ul aria-label="待上传文件" className="max-h-64 space-y-2 overflow-y-auto">{items.map((item) => <li className="flex items-center gap-3 rounded-md border border-border bg-surface-muted/50 px-3 py-2" key={`${item.file.name}-${item.file.size}-${item.file.lastModified}`}><FileText className="size-4 shrink-0 text-primary" /><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{item.file.name}</p><p className={`text-xs ${item.state === "failed" || item.state === "rejected" ? "text-danger" : "text-muted-foreground"}`}>{item.error ?? (item.state === "queued" ? "等待上传" : item.state === "uploading" ? "正在上传…" : item.state === "accepted" ? "已提交处理" : item.state === "skipped" ? "内容重复，已跳过" : "文件被拒绝")}</p></div>{item.state === "uploading" ? <LoaderCircle className="size-4 animate-spin text-primary" /> : item.state === "accepted" || item.state === "skipped" ? <CheckCircle2 className="size-4 text-success" /> : item.state === "failed" ? <RotateCcw className="size-4 text-danger" /> : item.state === "rejected" ? <X className="size-4 text-danger" /> : null}</li>)}</ul> : null}{error ? <p className="text-xs text-danger">{error}</p> : null}</div><div className="flex items-center justify-between gap-3 border-t border-border bg-surface-muted/50 px-6 py-4"><p className="text-xs text-muted-foreground">{items.length ? `已选择 ${items.length} 个文件` : "尚未选择文件"}</p><div className="flex gap-2"><Button onClick={() => onOpenChange(false)} type="button" variant="ghost">{finished ? "完成" : "取消"}</Button><Button disabled={!items.length || finished} loading={pending} type="submit">{failedCount ? `重试上传（${failedCount}）` : "上传并处理"}</Button></div></div></form></dialog>;
}
