"use client";

import { FormEvent, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Activity, Copy, KeyRound, Pencil, Plus, RefreshCw, RotateCcw, Server, Trash2, X } from "lucide-react";
import { apiClient } from "@/api";
import { useApiResource } from "@/api/use-api-resource";
import { Button, ConfirmDialog, EmptyState, ErrorState, LoadingState } from "@/components";
import type { CollectionDetail, MCPKeyMetadata } from "@/types";
import { MCPConnectionDiagnostics } from "./mcp-connection-diagnostics";

function maskKey(keyId: string) { return "sk-" + keyId.slice(0, 4) + "****************" + keyId.slice(-4); }
function when(value: string | null) { return value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "从未"; }

export function inferMcpServerUrl(hostname?: string) {
  const configured = process.env.NEXT_PUBLIC_MCP_SERVER_URL?.trim();
  if (configured) return configured.replace(/\/$/, "");
  return `http://${hostname || "127.0.0.1"}:8765/mcp`;
}

export function buildMcpClientConfig(url: string, apiKey = "<MCP_CLIENT_API_KEY>") {
  return JSON.stringify({
    name: "skdy-knowledge-query",
    transport: "streamable-http",
    url,
    headers: { Authorization: `Bearer ${apiKey}` },
  }, null, 2);
}

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(value); return; }
  const field = document.createElement("textarea"); field.value = value; field.setAttribute("readonly", ""); field.style.position = "fixed"; field.style.opacity = "0"; document.body.appendChild(field); field.select();
  const copied = document.execCommand("copy"); field.remove();
  if (!copied) throw new Error("copy failed");
}

const subscribeToStaticBrowserLocation = () => () => {};

export function MCPKeysView() {
  const load = useCallback((signal: AbortSignal) => apiClient.listMCPKeys(signal), []);
  const keys = useApiResource(load);
  const collections = useApiResource((signal) => apiClient.listCollections({ limit: 100 }, signal));
  const [open, setOpen] = useState(false); const [secret, setSecret] = useState<string>();
  const mcpUrl = useSyncExternalStore(
    subscribeToStaticBrowserLocation,
    () => inferMcpServerUrl(window.location.hostname),
    () => inferMcpServerUrl(),
  );
  const [target, setTarget] = useState<MCPKeyMetadata>(); const [editTarget, setEditTarget] = useState<MCPKeyMetadata>(); const [resetTarget, setResetTarget] = useState<MCPKeyMetadata>(); const [actionError, setActionError] = useState<unknown>();
  async function remove() { if (!target) return; try { await apiClient.deleteMCPKey(target.name); keys.retry(); } catch (e) { setActionError(e); } finally { setTarget(undefined); } }
  async function update(item: MCPKeyMetadata, name: string, allowedCollections: string[]) { try { setActionError(undefined); if ([...item.allowed_collections].sort().join("\0") !== [...allowedCollections].sort().join("\0")) await apiClient.updateMCPKeyCollections(item.name, allowedCollections); if (name.trim() !== item.name) await apiClient.renameMCPKey(item.name, name); keys.retry(); } catch (e) { setActionError(e); } finally { setEditTarget(undefined); } }
  async function reset(item: MCPKeyMetadata) { try { const next = await apiClient.rotateMCPKey(item.name); setSecret(next.api_key); keys.retry(); } catch (e) { setActionError(e); } finally { setResetTarget(undefined); } }
  return <div className="app-container space-y-6"><header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><div className="mb-3 flex items-center gap-2"><span className="font-mono text-xs font-semibold tracking-[0.14em] text-primary uppercase">MCP access</span></div><h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">API Key 管理</h1><p className="mt-2 max-w-2xl text-muted-foreground">为外部 Agent 签发独立凭证，并限制其可访问的知识库。</p></div><Button onClick={() => setOpen(true)}><Plus className="size-4" />创建 Key</Button></header><ConnectionCard mcpUrl={mcpUrl} /><MCPStatusCard /><div className="rounded-xl border border-warning/30 bg-warning/10 p-4 text-sm text-foreground">完整 Key 只会在创建或轮换后显示一次。请立即复制并安全交付给接入方。</div>{actionError ? <ErrorState description={actionError instanceof Error ? actionError.message : "操作失败"} title="操作失败" /> : null}{keys.loading ? <LoadingState label="加载 API Key" rows={4} /> : keys.error ? <ErrorState onRetry={keys.retry} title="无法加载 API Key" /> : !keys.data?.items.length ? <EmptyState action={<Button onClick={() => setOpen(true)}><Plus className="size-4" />创建第一个 Key</Button>} description="为外部 Agent 创建受知识库白名单限制的访问凭证。" icon={KeyRound} title="暂无 API Key" /> : <section className="overflow-hidden rounded-xl border border-border bg-surface"><table className="w-full text-left text-sm"><thead className="bg-surface-muted text-xs text-muted-foreground"><tr><th className="px-5 py-3">名称</th><th className="px-5 py-3">Key</th><th className="px-5 py-3">知识库范围</th><th className="px-5 py-3">创建日期</th><th className="px-5 py-3">最新使用日期</th><th className="px-5 py-3" /></tr></thead><tbody>{keys.data.items.map((item) => <tr className="border-t border-border" key={item.key_id}><td className="px-5 py-4 font-medium">{item.name}</td><td className="px-5 py-4 font-mono text-xs">{maskKey(item.key_id)}</td><td className="max-w-64 px-5 py-4 text-xs text-muted-foreground">{item.allowed_collections.join("、")}</td><td className="px-5 py-4 text-muted-foreground">{when(item.created_at)}</td><td className="px-5 py-4 text-muted-foreground">{when(item.last_used_at)}</td><td className="px-5 py-4"><div className="flex gap-2">{item.enabled ? <><Button aria-label="编辑 API Key" onClick={() => setEditTarget(item)} size="icon" title="编辑名称和权限" variant="ghost"><Pencil className="size-4" /></Button><Button aria-label="重置 API Key" onClick={() => setResetTarget(item)} size="icon" title="重置" variant="ghost"><RotateCcw className="size-4" /></Button><Button aria-label="删除 API Key" onClick={() => setTarget(item)} size="icon" title="删除" variant="ghost"><Trash2 className="size-4 text-danger" /></Button></> : null}</div></td></tr>)}</tbody></table></section>}<CreateDialog collections={collections.data?.items ?? []} onCreated={(key) => { setOpen(false); setSecret(key); keys.retry(); }} onOpenChange={setOpen} open={open} /><EditKeyDialog collections={collections.data?.items ?? []} item={editTarget} key={`edit-${editTarget?.key_id ?? "none"}`} onClose={() => setEditTarget(undefined)} onSubmit={update} /> <SecretDialog key={`secret-${secret ?? "none"}`} mcpUrl={mcpUrl} onClose={() => setSecret(undefined)} {...(secret ? { value: secret } : {})} /><ConfirmDialog confirmLabel="删除" description="删除后该 Key 将立即失效，且不能恢复。" destructive onConfirm={remove} onOpenChange={(value) => { if (!value) setTarget(undefined); }} open={Boolean(target)} title="删除 API Key？" /><ConfirmDialog confirmLabel="确认重置" description="重置后会生成新的 API Key，之前的 Key 将立即失效，使用旧 Key 的接入方需要更新配置。" onConfirm={() => reset(resetTarget!)} onOpenChange={(value) => { if (!value) setResetTarget(undefined); }} open={Boolean(resetTarget)} title="重置 API Key？" /></div>;
}

function MCPStatusCard() {
  const load = useCallback((signal: AbortSignal) => apiClient.getMCPServerStatus(signal), []);
  const status = useApiResource(load);
  const tone = status.data?.status === "online" ? "text-success" : status.data?.status === "degraded" ? "text-warning" : "text-danger";
  return <section className="rounded-xl border border-border bg-surface p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div className="flex gap-3"><span className="grid size-9 place-items-center rounded-md bg-primary/10 text-primary"><Activity className="size-4" /></span><div><h2 className="font-semibold">运行状态</h2>{status.loading ? <p className="mt-1 text-sm text-muted-foreground">正在检查 MCP Server…</p> : status.error ? <p className="mt-1 text-sm text-danger">无法读取 MCP Server 状态</p> : status.data ? <><p className={`mt-1 text-sm font-semibold ${tone}`}>{status.data.status === "online" ? "服务在线" : status.data.status === "degraded" ? "服务降级" : status.data.status === "misconfigured" ? "配置错误" : "服务离线"}</p><p className="mt-1 text-xs text-muted-foreground">上游 API：{status.data.upstream_status === "online" ? "在线" : status.data.upstream_status === "offline" ? "离线" : "未知"}{status.data.latency_ms !== null ? ` · ${Math.round(status.data.latency_ms)} ms` : ""} · 检查于 {when(status.data.checked_at)}</p></> : null}</div></div><Button aria-label="刷新 MCP 状态" disabled={status.loading} onClick={status.retry} size="icon" title="刷新状态" variant="ghost"><RefreshCw className={`size-4 ${status.loading ? "animate-spin motion-reduce:animate-none" : ""}`} /></Button></div></section>;
}

function ConnectionCard({ mcpUrl }: { mcpUrl: string }) {
  const [copied, setCopied] = useState(false);
  const healthUrl = mcpUrl.replace(/\/mcp$/, "/health");
  async function copyConfig() { try { await copyText(buildMcpClientConfig(mcpUrl)); setCopied(true); } catch { setCopied(false); } }
  return <section className="rounded-xl border border-border bg-surface p-5 sm:p-6"><div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start"><div><div className="flex items-center gap-2"><Server className="size-4 text-primary" /><h2 className="font-semibold">MCP Server 客户端接入</h2></div><p className="mt-1 text-sm text-muted-foreground">将下列地址与外部客户端 Key 交付给 MCP Client；不要提供内部服务密钥。</p></div><Button onClick={copyConfig} size="sm" variant="secondary"><Copy className="size-3.5" />{copied ? "已复制配置" : "复制配置模板"}</Button></div><dl className="mt-5 grid gap-4 text-sm sm:grid-cols-2"><div><dt className="text-xs text-muted-foreground">传输协议</dt><dd className="mt-1 font-medium">Streamable HTTP</dd></div><div><dt className="text-xs text-muted-foreground">认证方式</dt><dd className="mt-1 font-medium">Authorization: Bearer &lt;客户端 Key&gt;</dd></div><div className="sm:col-span-2"><dt className="text-xs text-muted-foreground">MCP URL</dt><dd className="mt-1 break-all rounded-md bg-surface-muted px-3 py-2 font-mono text-xs">{mcpUrl}</dd></div><div className="sm:col-span-2"><dt className="text-xs text-muted-foreground">匿名健康检查</dt><dd className="mt-1 break-all font-mono text-xs text-muted-foreground">{healthUrl}</dd></div></dl><p className="mt-4 text-xs text-muted-foreground">公网部署请通过 <code>NEXT_PUBLIC_MCP_SERVER_URL</code> 配置 HTTPS 反向代理地址。</p></section>;
}

function CreateDialog({ collections, onCreated, onOpenChange, open }: { collections: CollectionDetail[]; onCreated: (key: string) => void; onOpenChange: (open: boolean) => void; open: boolean }) { const ref = useRef<HTMLDialogElement>(null); const [pending, setPending] = useState(false); useEffect(() => { if (open && !ref.current?.open) ref.current?.showModal(); if (!open && ref.current?.open) ref.current.close(); }, [open]); async function submit(e: FormEvent<HTMLFormElement>) { e.preventDefault(); const form = new FormData(e.currentTarget); const allowed_collections = collections.filter((c) => form.getAll("collection").includes(c.name)).map((c) => c.name); setPending(true); try { const result = await apiClient.createMCPKey({ name: String(form.get("name") ?? ""), allowed_collections }); onCreated(result.api_key); } finally { setPending(false); } } return <dialog className="m-auto w-[min(calc(100%-2rem),34rem)] rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-lg backdrop:bg-foreground/20" ref={ref}><form onSubmit={submit}><div className="flex items-start justify-between border-b border-border p-6"><div><h2 className="text-lg font-semibold">创建 API Key</h2><p className="mt-1 text-xs text-muted-foreground">选择允许访问的知识库。</p></div><button onClick={() => onOpenChange(false)} type="button"><X className="size-4" /></button></div><div className="space-y-4 p-6"><label className="block text-sm font-medium">接入方名称<input autoFocus className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3" name="name" required /></label><fieldset><legend className="text-sm font-medium">知识库白名单</legend><div className="mt-2 max-h-48 space-y-2 overflow-auto rounded-md border border-border p-3">{collections.map((c) => <label className="flex gap-2 text-sm" key={c.id}><input name="collection" type="checkbox" value={c.name} />{c.name}</label>)}</div></fieldset></div><div className="flex justify-end gap-2 border-t border-border p-4"><Button onClick={() => onOpenChange(false)} variant="ghost">取消</Button><Button disabled={!collections.length} loading={pending} type="submit">创建并显示 Key</Button></div></form></dialog>; }
function EditKeyDialog({ collections, item, onClose, onSubmit }: { collections: CollectionDetail[]; item?: MCPKeyMetadata | undefined; onClose: () => void; onSubmit: (item: MCPKeyMetadata, name: string, allowedCollections: string[]) => Promise<void> }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => { if (item && !ref.current?.open) ref.current?.showModal(); if (!item && ref.current?.open) ref.current.close(); }, [item]);
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!item) return; const form = new FormData(event.currentTarget); const allowedCollections = form.getAll("collection").map(String); setPending(true); try { await onSubmit(item, String(form.get("name") ?? ""), allowedCollections); } finally { setPending(false); } }
  return <dialog className="m-auto w-[min(calc(100%-2rem),34rem)] rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-lg backdrop:bg-foreground/20" ref={ref}><form onSubmit={submit}><div className="flex items-start justify-between border-b border-border p-6"><div><h2 className="text-lg font-semibold">编辑 API Key</h2><p className="mt-1 text-xs text-muted-foreground">修改名称或知识库权限不会更换当前 Key，新权限立即生效。</p></div><button onClick={onClose} type="button"><X className="size-4" /></button></div><div className="space-y-4 p-6"><label className="block text-sm font-medium">名称<input autoFocus className="mt-2 h-10 w-full rounded-md border border-border bg-surface px-3" defaultValue={item?.name} name="name" required /></label><fieldset><legend className="text-sm font-medium">知识库白名单</legend><div className="mt-2 max-h-48 space-y-2 overflow-auto rounded-md border border-border p-3">{collections.map((collection) => <label className="flex gap-2 text-sm" key={collection.id}><input defaultChecked={item?.allowed_collections.includes(collection.name)} name="collection" type="checkbox" value={collection.name} />{collection.name}</label>)}</div><p className="mt-2 text-xs text-muted-foreground">至少选择一个知识库。</p></fieldset></div><div className="flex justify-end gap-2 border-t border-border p-4"><Button onClick={onClose} variant="ghost">取消</Button><Button disabled={!collections.length} loading={pending} type="submit">保存</Button></div></form></dialog>;
}

function SecretDialog({ value, mcpUrl, onClose }: { value?: string; mcpUrl: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [copyState, setCopyState] = useState<"idle" | "key" | "config" | "failed">("idle");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<Awaited<ReturnType<typeof apiClient.testMCPConnection>>>();
  useEffect(() => { if (value && !ref.current?.open) ref.current?.showModal(); if (!value && ref.current?.open) ref.current.close(); }, [value]);
  async function copy(kind: "key" | "config") {
    if (!value) return;
    try { await copyText(kind === "key" ? value : buildMcpClientConfig(mcpUrl, value)); setCopyState(kind); } catch { setCopyState("failed"); }
  }
  async function testConnection() { if (!value) return; setTesting(true); setTestResult(undefined); try { setTestResult(await apiClient.testMCPConnection(value)); } catch (reason) { setTestResult({ ok: false, stages: [{ name: "connect", status: "failed" }, { name: "initialize", status: "skipped" }, { name: "tools_list", status: "skipped" }, { name: "list_collections", status: "skipped" }], error: { code: "request_failed", message: "连通测试请求失败", suggested_action: reason instanceof Error ? reason.message : "请检查主 API 服务。" }, tested_at: new Date().toISOString() }); } finally { setTesting(false); } }
  return <dialog className="m-auto max-h-[calc(100dvh-2rem)] w-[min(calc(100%-2rem),42rem)] overflow-y-auto rounded-xl border border-border bg-surface-raised p-6 text-foreground shadow-lg" ref={ref}><h2 className="text-lg font-semibold">请立即保存 API Key</h2><p className="mt-2 text-sm text-warning">关闭后将无法再次查看完整 Key。可直接复制包含地址和认证 Header 的客户端配置。</p><code className="mt-4 block break-all rounded-md bg-surface-muted p-3 text-sm">{value}</code><pre className="mt-3 max-h-56 overflow-auto rounded-md bg-surface-muted p-3 text-xs"><code>{value ? buildMcpClientConfig(mcpUrl, value) : ""}</code></pre>{copyState === "key" ? <p className="mt-2 text-sm text-success">Key 已复制。</p> : copyState === "config" ? <p className="mt-2 text-sm text-success">客户端配置已复制。</p> : copyState === "failed" ? <p className="mt-2 text-sm text-danger">浏览器阻止了复制，请手动选择上方内容。</p> : null}{testResult ? <MCPConnectionDiagnostics result={testResult} /> : null}<div className="mt-5 flex flex-wrap justify-end gap-2"><Button loading={testing} onClick={() => void testConnection()} variant="secondary"><Activity className="size-4" />测试连接</Button><Button onClick={() => copy("key")} variant="secondary"><Copy className="size-4" />复制 Key</Button><Button onClick={() => copy("config")} variant="secondary"><Copy className="size-4" />复制客户端配置</Button><Button onClick={onClose}>我已保存</Button></div></dialog>;
}
