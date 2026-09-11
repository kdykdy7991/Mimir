import { CheckCircle2, CircleDashed, Clock3, LoaderCircle, XCircle } from "lucide-react";
import type { MCPConnectionTestResponse } from "@/types";
import { mcpDiagnosticGuidance } from "./mcp-diagnostics-guidance";

const LABELS = {
  connect: "连接 MCP Server",
  initialize: "协议握手",
  tools_list: "读取工具列表",
  list_collections: "验证知识库权限",
} as const;

const STATUS = {
  pending: { label: "等待中", icon: Clock3, className: "text-muted-foreground" },
  running: { label: "测试中", icon: LoaderCircle, className: "text-info" },
  success: { label: "通过", icon: CheckCircle2, className: "text-success" },
  failed: { label: "失败", icon: XCircle, className: "text-danger" },
  skipped: { label: "未执行", icon: CircleDashed, className: "text-muted-foreground" },
} as const;

export function MCPConnectionDiagnostics({ result }: { result: MCPConnectionTestResponse }) {
  return <section aria-label="MCP 连通测试结果" className="mt-5 rounded-lg border border-border bg-surface p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">连通测试</h3><span className={`text-sm font-semibold ${result.ok ? "text-success" : "text-danger"}`}>{result.ok ? "全部通过" : "测试未通过"}</span></div>
    <ol className="mt-4 space-y-3">{result.stages.map((stage) => {
      const definition = STATUS[stage.status];
      const Icon = definition.icon;
      const statistic = stage.tool_count !== undefined && stage.tool_count !== null ? `${stage.tool_count} 个工具` : stage.collection_count !== undefined && stage.collection_count !== null ? `${stage.collection_count} 个知识库` : null;
      return <li className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-3 text-sm" key={stage.name}><Icon aria-hidden="true" className={`size-4 ${definition.className} ${stage.status === "running" ? "animate-spin motion-reduce:animate-none" : ""}`} /><div className="min-w-0"><p className="font-medium">{LABELS[stage.name]}</p>{statistic ? <p className="text-xs text-muted-foreground">{statistic}</p> : null}</div><div className="text-right"><p className={definition.className}>{definition.label}</p>{stage.latency_ms !== undefined && stage.latency_ms !== null ? <p className="font-mono text-[0.6875rem] text-muted-foreground">{Math.round(stage.latency_ms)} ms</p> : null}</div></li>;
    })}</ol>
    {result.error ? <div className="mt-4 rounded-md border border-danger/20 bg-danger/5 p-3"><p className="text-sm font-semibold text-danger">{result.error.message}</p><p className="mt-1 text-xs text-muted-foreground">{mcpDiagnosticGuidance(result.error.code, result.error.suggested_action)}</p>{result.error.request_id ? <p className="mt-2 break-all font-mono text-[0.6875rem] text-muted-foreground">Request ID: {result.error.request_id}</p> : null}</div> : null}
    <p className="mt-3 text-right text-[0.6875rem] text-muted-foreground">测试于 {new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "medium" }).format(new Date(result.tested_at))}</p>
  </section>;
}
