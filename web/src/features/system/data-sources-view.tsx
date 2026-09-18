"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ChevronRight, DatabaseZap, Pause, Play, PlugZap, Plus, RefreshCw, RotateCcw, X } from "lucide-react";
import { apiClient } from "@/api";
import { useApiResource } from "@/api/use-api-resource";
import { Button, EmptyState, ErrorState, LoadingState, StatusBadge } from "@/components";
import type { CollectionDetail, DataSourceConnectorType, DataSourceInfo, SyncRun } from "@/types";

function when(value: number | null | undefined) {
  return value == null ? "从未" : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value * 1000));
}

function endpoint(source: DataSourceInfo) {
  const value = source.policy.url ?? source.policy.feed_url;
  return typeof value === "string" ? value : "未配置地址";
}

export function DataSourcesView() {
  const load = useCallback((signal: AbortSignal) => apiClient.listDataSources(undefined, signal), []);
  const sources = useApiResource(load);
  const collections = useApiResource((signal) => apiClient.listCollections({ limit: 100 }, signal));
  const [creating, setCreating] = useState(false);
  const [selectedId, setSelectedId] = useState<string>();
  const setSelected = (source: DataSourceInfo) => setSelectedId(source.id);
  const [actionId, setActionId] = useState<string>();
  const [actionError, setActionError] = useState<unknown>();

  const selected = sources.data?.data_sources.find((item) => item.id === selectedId);

  async function toggle(source: DataSourceInfo) {
    setActionId(source.id); setActionError(undefined);
    try {
      await (source.enabled ? apiClient.pauseDataSource(source.id) : apiClient.resumeDataSource(source.id));
      sources.retry();
    } catch (reason) { setActionError(reason); }
    finally { setActionId(undefined); }
  }

  async function sync(source: DataSourceInfo) {
    setActionId(source.id); setActionError(undefined);
    try { await apiClient.enqueueDataSourceSync(source.id); }
    catch (reason) { setActionError(reason); }
    finally { setActionId(undefined); }
  }

  return <div className="app-container space-y-6">
    <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><p className="mb-3 font-mono text-xs font-semibold tracking-[0.14em] text-primary uppercase">External sync</p><h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">数据源管理</h1><p className="mt-2 max-w-2xl text-muted-foreground">配置受控 URL 或 RSS/Atom 增量同步，查看最近运行、失败与待人工处理的冲突。</p></div><Button onClick={() => setCreating(true)}><Plus className="size-4" />添加数据源</Button></header>
    <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 text-sm"><span className="font-semibold">安全边界：</span>凭证仅在创建时提交，页面和后续 API 响应均不会回显。</div>
    <ConnectionTestPanel sources={sources.data?.data_sources ?? []} />
    <DeadLetterPanel sources={sources.data?.data_sources ?? []} />
    {actionError ? <ErrorState description={actionError instanceof Error ? actionError.message : "操作失败"} title="数据源操作失败" /> : null}
    {sources.loading ? <LoadingState label="加载数据源" rows={4} /> : sources.error ? <ErrorState onRetry={sources.retry} title="无法加载数据源" /> : !sources.data?.data_sources.length ? <EmptyState action={<Button onClick={() => setCreating(true)}><Plus className="size-4" />添加第一个数据源</Button>} description="连接 RSS/Atom 或公开受控 URL，并同步到指定知识库。" icon={DatabaseZap} title="暂无数据源" /> : <section className="grid gap-4 lg:grid-cols-2">{sources.data.data_sources.map((source) => <article className="rounded-xl border border-border bg-surface p-5" key={source.id}><div className="flex items-start justify-between gap-4"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">{source.name}</h2><StatusBadge label={source.enabled ? "已启用" : "已暂停"} status={source.enabled ? "ready" : "cancelled"} /><span className="rounded-full bg-surface-muted px-2 py-1 font-mono text-[0.6875rem] uppercase text-muted-foreground">{source.connector_type}</span></div><p className="mt-2 truncate text-sm text-muted-foreground" title={endpoint(source)}>{endpoint(source)}</p></div><Button aria-label={source.enabled ? "暂停数据源" : "恢复数据源"} loading={actionId === source.id} onClick={() => void toggle(source)} size="icon" title={source.enabled ? "暂停" : "恢复"} variant="ghost">{source.enabled ? <Pause className="size-4" /> : <Play className="size-4" />}</Button></div><dl className="mt-5 grid grid-cols-2 gap-4 border-t border-border pt-4 text-sm"><div><dt className="text-xs text-muted-foreground">知识库 ID</dt><dd className="mt-1 truncate font-mono text-xs" title={source.collection_id}>{source.collection_id}</dd></div><div><dt className="text-xs text-muted-foreground">检查点版本</dt><dd className="mt-1 font-medium">{source.checkpoint_revision}</dd></div><div><dt className="text-xs text-muted-foreground">创建时间</dt><dd className="mt-1">{when(source.created_at)}</dd></div><div><dt className="text-xs text-muted-foreground">更新时间</dt><dd className="mt-1">{when(source.updated_at)}</dd></div></dl><div className="mt-4 grid grid-cols-2 gap-2"><Button disabled={!source.enabled} loading={actionId === source.id} onClick={() => void sync(source)}><RefreshCw className="size-4" />立即同步</Button><Button className="justify-between" onClick={() => setSelected(source)} variant="secondary">查看详情<ChevronRight className="size-4" /></Button></div></article>)}</section>}
    <CreateDataSourceDialog collections={collections.data?.items ?? []} onClose={() => setCreating(false)} onCreated={() => { setCreating(false); sources.retry(); }} open={creating} />
    <SyncDetailsDialog key={selected?.id ?? "none"} onClose={() => setSelectedId(undefined)} source={selected} />
  </div>;
}

function CreateDataSourceDialog({ collections, onClose, onCreated, open }: { collections: CollectionDetail[]; onClose: () => void; onCreated: () => void; open: boolean }) {
  const ref = useRef<HTMLDialogElement>(null); const [pending, setPending] = useState(false); const [error, setError] = useState<unknown>();
  useEffect(() => { if (open && !ref.current?.open) ref.current?.showModal(); if (!open && ref.current?.open) ref.current.close(); }, [open]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setError(undefined); const form = new FormData(event.currentTarget);
    const credentials: Record<string, string> = {}; const token = String(form.get("token") ?? "").trim(); if (token) credentials.token = token;
    try { await apiClient.createDataSource({ name: String(form.get("name") ?? ""), connector_type: String(form.get("connector_type")) as DataSourceConnectorType, collection_id: String(form.get("collection_id") ?? ""), policy: { url: String(form.get("url") ?? "") }, credentials }); event.currentTarget.reset(); onCreated(); }
    catch (reason) { setError(reason); } finally { setPending(false); }
  }
  return <dialog className="m-auto w-[min(calc(100%-2rem),36rem)] rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-lg backdrop:bg-foreground/20" ref={ref}><form onSubmit={submit}><div className="flex items-start justify-between border-b border-border p-6"><div><h2 className="text-lg font-semibold">添加数据源</h2><p className="mt-1 text-xs text-muted-foreground">远端地址会在同步前执行 SSRF 与重定向校验。</p></div><button aria-label="关闭" onClick={onClose} type="button"><X className="size-4" /></button></div><div className="space-y-4 p-6">{error ? <ErrorState description={error instanceof Error ? error.message : "创建失败"} title="无法创建数据源" /> : null}<label className="block text-sm font-medium">名称<input autoFocus className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3" name="name" required /></label><label className="block text-sm font-medium">类型<select className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3" defaultValue="rss" name="connector_type"><option value="rss">RSS / Atom</option><option value="url">受控 URL</option></select></label><label className="block text-sm font-medium">目标知识库<select className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3" name="collection_id" required><option value="">请选择</option>{collections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="block text-sm font-medium">远端 URL<input className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3" name="url" placeholder="https://example.com/feed.xml" required type="url" /></label><label className="block text-sm font-medium">Bearer Token（可选）<input autoComplete="new-password" className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3" name="token" type="password" /><span className="mt-1 block text-xs font-normal text-muted-foreground">只随本次创建请求提交，不会回显。</span></label></div><div className="flex justify-end gap-2 border-t border-border p-4"><Button onClick={onClose} variant="ghost">取消</Button><Button disabled={!collections.length} loading={pending} type="submit">创建数据源</Button></div></form></dialog>;
}

function SyncDetailsDialog({ onClose, source }: { onClose: () => void; source: DataSourceInfo | undefined }) {
  const ref = useRef<HTMLDialogElement>(null); const sourceId = source?.id;
  const loadStatus = useCallback((signal: AbortSignal) => sourceId ? apiClient.getDataSourceSyncStatus(sourceId, signal) : Promise.reject(new Error("missing source")), [sourceId]);
  const loadFailures = useCallback((signal: AbortSignal) => sourceId ? apiClient.listDataSourceFailures(sourceId, 50, signal) : Promise.reject(new Error("missing source")), [sourceId]);
  const loadConflicts = useCallback((signal: AbortSignal) => sourceId ? apiClient.listDataSourceConflicts(sourceId, signal) : Promise.reject(new Error("missing source")), [sourceId]);
  const status = useApiResource(loadStatus); const failures = useApiResource(loadFailures); const conflicts = useApiResource(loadConflicts);
  const [acknowledging, setAcknowledging] = useState<string>(); const [actionError, setActionError] = useState<unknown>();
  useEffect(() => { if (source && !ref.current?.open) ref.current?.showModal(); if (!source && ref.current?.open) ref.current.close(); }, [source]);
  const last = status.data?.last_run;
  async function acknowledge(conflictId: string) {
    if (!sourceId) return; setAcknowledging(conflictId); setActionError(undefined);
    try { await apiClient.acknowledgeDataSourceConflict(sourceId, conflictId); conflicts.retry(); }
    catch (reason) { setActionError(reason); } finally { setAcknowledging(undefined); }
  }
  const refresh = () => { status.retry(); failures.retry(); conflicts.retry(); };
  return <dialog className="m-auto max-h-[calc(100dvh-2rem)] w-[min(calc(100%-2rem),48rem)] overflow-y-auto rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-lg backdrop:bg-foreground/20" ref={ref}><div className="sticky top-0 z-10 flex items-start justify-between border-b border-border bg-surface-raised p-6"><div><h2 className="text-lg font-semibold">{source?.name} · 同步详情</h2><p className="mt-1 text-xs text-muted-foreground">确认人工修订与远端差异后，可将冲突标记为已人工处理；此操作不会隐式覆盖文档。</p></div><div className="flex gap-1"><Button aria-label="刷新同步详情" onClick={refresh} size="icon" title="刷新" variant="ghost"><RefreshCw className="size-4" /></Button><button aria-label="关闭" className="grid size-9 place-items-center" onClick={onClose} type="button"><X className="size-4" /></button></div></div><div className="space-y-6 p-6">{actionError ? <ErrorState description={actionError instanceof Error ? actionError.message : "操作失败"} title="无法确认冲突" /> : null}{status.loading ? <LoadingState label="加载同步状态" rows={2} /> : status.error ? <ErrorState onRetry={status.retry} title="同步状态加载失败" /> : <section><h3 className="text-sm font-semibold">最近一次运行</h3>{last ? <RunSummary run={last} /> : <p className="mt-3 rounded-lg bg-surface-muted p-4 text-sm text-muted-foreground">尚无同步运行记录。</p>}</section>}<section><div className="flex items-center justify-between"><h3 className="text-sm font-semibold">待处理冲突</h3>{conflicts.data?.count ? <span className="text-xs font-semibold text-warning">{conflicts.data.count} 个待处理</span> : null}</div>{conflicts.loading ? <div className="mt-3"><LoadingState label="加载冲突" rows={2} /></div> : conflicts.error ? <div className="mt-3"><ErrorState onRetry={conflicts.retry} title="冲突加载失败" /></div> : !conflicts.data?.conflicts.length ? <p className="mt-3 rounded-lg bg-success/10 p-4 text-sm text-success">暂无待处理冲突。</p> : <div className="mt-3 space-y-2">{conflicts.data.conflicts.map((conflict) => <div className="rounded-lg border border-warning/30 bg-warning/10 p-4" key={conflict.id}><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><AlertTriangle className="size-4 text-warning" /><span className="text-sm font-semibold">人工修订与远端更新冲突</span></div><p className="mt-2 font-mono text-xs text-muted-foreground">文档 {conflict.document_id}</p><p className="mt-1 text-xs text-muted-foreground">当前版本 {conflict.active_revision_id} · 远端版本 {conflict.remote_revision}</p></div><Button loading={acknowledging === conflict.id} onClick={() => void acknowledge(conflict.id)} size="sm" variant="secondary">标记已人工处理</Button></div></div>)}</div>}</section><section><h3 className="text-sm font-semibold">历史失败运行</h3>{failures.loading ? <div className="mt-3"><LoadingState label="加载失败记录" rows={3} /></div> : failures.error ? <div className="mt-3"><ErrorState onRetry={failures.retry} title="失败记录加载失败" /></div> : !failures.data?.failures.length ? <p className="mt-3 rounded-lg bg-surface-muted p-4 text-sm text-muted-foreground">暂无失败运行记录。</p> : <div className="mt-3 space-y-2">{failures.data.failures.map((run) => <div className={`rounded-lg border p-4 ${run.status === "conflict" ? "border-warning/30 bg-warning/10" : "border-danger/20 bg-danger/5"}`} key={run.id}><div className="flex flex-wrap items-center justify-between gap-2"><StatusBadge label={run.status === "conflict" ? "冲突运行" : "同步失败"} status={run.status} /><span className="text-xs text-muted-foreground">{when(run.started_at)}</span></div><p className="mt-2 font-mono text-xs text-muted-foreground">{run.error_code ?? "未提供错误码"}</p><p className="mt-2 text-xs text-muted-foreground">新增 {run.added} · 更新 {run.updated} · 删除 {run.deleted} · 冲突 {run.conflicts}</p></div>)}</div>}</section></div></dialog>;
}

function RunSummary({ run }: { run: SyncRun }) {
  return <div className="mt-3 rounded-lg border border-border bg-surface p-4"><div className="flex items-center justify-between gap-3"><StatusBadge status={run.status} /><span className="text-xs text-muted-foreground">{when(run.started_at)}</span></div><dl className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4"><div><dt className="text-xs text-muted-foreground">新增</dt><dd className="mt-1 font-semibold">{run.added}</dd></div><div><dt className="text-xs text-muted-foreground">更新</dt><dd className="mt-1 font-semibold">{run.updated}</dd></div><div><dt className="text-xs text-muted-foreground">删除</dt><dd className="mt-1 font-semibold">{run.deleted}</dd></div><div><dt className="text-xs text-muted-foreground">冲突</dt><dd className="mt-1 font-semibold">{run.conflicts}</dd></div></dl></div>;
}

function ConnectionTestPanel({ sources }: { sources: DataSourceInfo[] }) {
  const [sourceId, setSourceId] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<"ok" | "error">();
  async function testConnection() {
    if (!sourceId) return;
    setPending(true); setResult(undefined);
    try { await apiClient.testDataSourceConnection(sourceId); setResult("ok"); }
    catch { setResult("error"); } finally { setPending(false); }
  }
  return <section className="rounded-xl border border-border bg-surface p-5"><div className="flex items-start gap-3"><span className="grid size-9 place-items-center rounded-md bg-primary/10 text-primary"><PlugZap className="size-4" /></span><div className="flex-1"><h2 className="font-semibold">连接测试</h2><p className="mt-1 text-xs text-muted-foreground">执行 DNS、SSRF 和 Connector 配置校验，不触发同步或推进 checkpoint。</p><div className="mt-4 flex flex-col gap-2 sm:flex-row"><select aria-label="选择待测试数据源" className="h-10 min-w-0 flex-1 rounded-md border border-border bg-surface px-3 text-sm" onChange={(event) => { setSourceId(event.target.value); setResult(undefined); }} value={sourceId}><option value="">选择数据源</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select><Button disabled={!sourceId} loading={pending} onClick={() => void testConnection()} variant="secondary">测试连接</Button></div>{result === "ok" ? <p className="mt-2 text-sm text-success">连接配置与安全策略校验通过。</p> : result === "error" ? <p className="mt-2 text-sm text-danger">连接测试失败，请检查地址、DNS、凭据或网络策略。</p> : null}</div></div></section>;
}

function DeadLetterPanel({ sources }: { sources: DataSourceInfo[] }) {
  const [sourceId, setSourceId] = useState("");
  const [items, setItems] = useState<Awaited<ReturnType<typeof apiClient.listDataSourceDeadLetters>>["dead_letters"]>([]);
  const [pending, setPending] = useState(false); const [replaying, setReplaying] = useState<string>(); const [error, setError] = useState(false);
  async function load(nextId = sourceId) {
    if (!nextId) { setItems([]); return; }
    setPending(true); setError(false);
    try { setItems((await apiClient.listDataSourceDeadLetters(nextId)).dead_letters); }
    catch { setError(true); } finally { setPending(false); }
  }
  async function replay(taskId: string) {
    if (!sourceId) return; setReplaying(taskId); setError(false);
    try { await apiClient.replayDataSourceDeadLetter(sourceId, taskId); await load(); }
    catch { setError(true); } finally { setReplaying(undefined); }
  }
  return <section className="rounded-xl border border-border bg-surface p-5"><div className="flex items-center gap-2"><RotateCcw className="size-4 text-primary" /><h2 className="font-semibold">同步死信</h2></div><p className="mt-1 text-xs text-muted-foreground">仅显示选中数据源的 Sync 队列死信；重放保留原 attempt 和事件历史。</p><div className="mt-4 flex flex-col gap-2 sm:flex-row"><select aria-label="选择死信数据源" className="h-10 min-w-0 flex-1 rounded-md border border-border bg-surface px-3 text-sm" onChange={(event) => { const value = event.target.value; setSourceId(value); void load(value); }} value={sourceId}><option value="">选择数据源</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select><Button disabled={!sourceId} loading={pending} onClick={() => void load()} variant="secondary">刷新</Button></div>{error ? <p className="mt-3 text-sm text-danger">死信操作失败。</p> : null}{sourceId && !pending && !items.length ? <p className="mt-3 text-sm text-muted-foreground">暂无同步死信。</p> : null}{items.length ? <div className="mt-4 space-y-2">{items.map((item) => <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/20 bg-danger/5 p-3" key={item.task_id}><div><p className="font-mono text-xs">{item.task_id}</p><p className="mt-1 text-xs text-muted-foreground">{item.error_code ?? "sync_failed"} · attempt {item.attempt}/{item.max_attempts}</p></div><Button loading={replaying === item.task_id} onClick={() => void replay(item.task_id)} size="sm" variant="secondary"><RotateCcw className="size-3.5" />重放</Button></div>)}</div> : null}</section>;
}
