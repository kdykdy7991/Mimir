"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  Clock3,
  Copy,
  GripVertical,
  Route,
  X,
} from "lucide-react";
import { StatusBadge } from "@/components";
import type { TraceResponse } from "@/types";
import {
  normalizeTraceStatus,
  shouldShowRawStageName,
  traceStageDescription,
  traceStageLabel,
} from "./trace-presentation";

type TraceStage = NonNullable<TraceResponse["stages"]>[number];
type DetailTab = "overview" | "io" | "metadata";
const WIDTH_KEY = "skdy.document-trace-drawer-width";
const MIN_WIDTH = 520;
const DEFAULT_WIDTH = 760;

function formatDuration(value: number) {
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(2)} 秒`;
  return `${(value / 60_000).toFixed(1)} 分钟`;
}

function safeDetails(stage: TraceStage) {
  return Object.fromEntries(
    Object.entries(stage.details ?? {}).filter(
      ([key]) => key !== "__proto__" && key !== "constructor" && key !== "prototype",
    ),
  );
}

function stageStatus(stage: TraceStage) {
  if (stage.error_code || stage.error_summary) return "failed";
  if (stage.details?.event === "skipped") return "skipped";
  return normalizeTraceStatus(stage.status ?? "success");
}

function StageDetails({ stage }: { stage: TraceStage }) {
  const [tab, setTab] = useState<DetailTab>("overview");
  const details = safeDetails(stage);
  const description = traceStageDescription(stage);
  const tabs: Array<{ id: DetailTab; label: string }> = [
    { id: "overview", label: "概览" },
    { id: "io", label: "输入输出" },
    { id: "metadata", label: "元数据" },
  ];
  return (
    <div className="border-t border-border bg-surface-muted/30 px-4 pb-4">
      <div className="flex gap-1 pt-3" role="tablist" aria-label="阶段详情">
        {tabs.map((item) => (
          <button
            aria-selected={tab === item.id}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold ${tab === item.id ? "bg-surface text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
            key={item.id}
            onClick={() => setTab(item.id)}
            role="tab"
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>
      {tab === "overview" ? (
        <dl className="mt-3 grid gap-3 text-xs sm:grid-cols-2">
          <div><dt className="text-muted-foreground">业务阶段</dt><dd className="mt-1 font-medium">{traceStageLabel(stage)}</dd></div>
          <div><dt className="text-muted-foreground">原始名称</dt><dd className="mt-1 font-mono">{stage.name}</dd></div>
          <div><dt className="text-muted-foreground">开始时间</dt><dd className="mt-1">{new Date(stage.started_at).toLocaleString("zh-CN")}</dd></div>
          <div><dt className="text-muted-foreground">执行耗时</dt><dd className="mt-1 font-mono">{formatDuration(stage.duration_ms)}</dd></div>
          <div><dt className="text-muted-foreground">实现方式</dt><dd className="mt-1">{stage.method ?? "—"}</dd></div>
          <div><dt className="text-muted-foreground">Provider</dt><dd className="mt-1">{stage.provider ?? "—"}</dd></div>
          {description ? <div className="sm:col-span-2"><dt className="text-muted-foreground">说明</dt><dd className="mt-1">{description}</dd></div> : null}
        </dl>
      ) : tab === "io" ? (
        <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
          <div className="rounded-md border border-border bg-surface p-3"><dt className="text-muted-foreground">输入数量</dt><dd className="mt-1 text-lg font-semibold">{stage.input_count ?? "—"}</dd></div>
          <div className="rounded-md border border-border bg-surface p-3"><dt className="text-muted-foreground">输出数量</dt><dd className="mt-1 text-lg font-semibold">{stage.output_count ?? "—"}</dd></div>
          <p className="col-span-2 text-muted-foreground">当前服务端只提供安全的数量摘要，不展示原始文档正文或模型提示词。</p>
        </dl>
      ) : (
        <pre className="mt-3 max-h-64 overflow-auto rounded-md border border-border bg-surface p-3 text-xs leading-5">{Object.keys(details).length ? JSON.stringify(details, null, 2) : "没有可用元数据"}</pre>
      )}
    </div>
  );
}

function WaterfallStage({
  stage,
  traceStart,
  totalDuration,
  selected,
  onSelect,
}: {
  stage: TraceStage;
  traceStart: number;
  totalDuration: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const start = Math.max(0, new Date(stage.started_at).getTime() - traceStart);
  const left = Math.min(98, (start / totalDuration) * 100);
  const width = Math.max(1.5, Math.min(100 - left, (stage.duration_ms / totalDuration) * 100));
  const status = stageStatus(stage);
  const barColor = status === "failed" ? "bg-danger" : status === "running" ? "bg-info animate-pulse" : status === "skipped" ? "bg-muted-foreground" : "bg-primary";
  return (
    <div className={`overflow-hidden rounded-lg border ${selected ? "border-primary ring-1 ring-primary/20" : "border-border"}`}>
      <button className="grid w-full gap-3 bg-surface p-4 text-left hover:bg-surface-muted/40 sm:grid-cols-[minmax(10rem,0.8fr)_minmax(14rem,1.2fr)_5rem] sm:items-center" onClick={onSelect} type="button">
        <span className="min-w-0">
          <span className="flex items-center gap-2"><span className={`size-2.5 shrink-0 rounded-full ${barColor}`} /><span className="truncate text-sm font-semibold">{traceStageLabel(stage)}</span></span>
          <span className="mt-1 block truncate text-xs text-muted-foreground">{stage.provider ?? stage.method ?? (shouldShowRawStageName(stage) ? stage.name : "处理阶段")}</span>
        </span>
        <span className="relative h-7 overflow-hidden rounded bg-surface-muted" aria-label={`${traceStageLabel(stage)} 时间位置`}>
          <span className={`absolute inset-y-1 rounded ${barColor}`} style={{ left: `${left}%`, width: `${width}%` }} />
        </span>
        <span className="text-right font-mono text-xs font-semibold">{status === "skipped" ? "—" : formatDuration(stage.duration_ms)}</span>
      </button>
      {selected ? <StageDetails stage={stage} /> : null}
    </div>
  );
}

export function DocumentTraceDrawer({
  open,
  onClose,
  trace,
}: {
  open: boolean;
  onClose: () => void;
  trace: TraceResponse;
}) {
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [liveNow, setLiveNow] = useState(0);
  useEffect(() => {
    const stored = Number(window.localStorage.getItem(WIDTH_KEY));
    if (!Number.isFinite(stored) || stored < MIN_WIDTH) return;
    const frame = window.requestAnimationFrame(() => setWidth(stored));
    return () => window.cancelAnimationFrame(frame);
  }, []);
  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);
  useEffect(() => {
    if (!open || normalizeTraceStatus(trace.status) !== "running") return;
    const timer = window.setInterval(() => setLiveNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [open, trace.status]);
  const stages = useMemo(
    () => [...(trace.stages ?? [])].sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime()),
    [trace.stages],
  );
  if (!open) return null;
  const status = trace.error ? "failed" : normalizeTraceStatus(trace.status ?? "success");
  const traceStart = new Date(trace.started_at).getTime();
  const observedEnd = stages.reduce((latest, stage) => Math.max(latest, new Date(stage.started_at).getTime() + stage.duration_ms), traceStart);
  const liveEnd = status === "running" && liveNow > 0 ? liveNow : observedEnd;
  const totalDuration = Math.max(1, trace.total_latency_ms, liveEnd - traceStart);
  function startResize(event: React.PointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const move = (moveEvent: PointerEvent) => setWidth(Math.min(window.innerWidth - 24, Math.max(MIN_WIDTH, startWidth + startX - moveEvent.clientX)));
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      setWidth((value) => { window.localStorage.setItem(WIDTH_KEY, String(value)); return value; });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }
  return (
    <div className="fixed inset-0 z-50 bg-foreground/25" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside aria-label="文档处理链路" aria-modal="true" className="absolute inset-y-0 right-0 flex max-w-[calc(100vw-1rem)] flex-col border-l border-border bg-background shadow-2xl" role="dialog" style={{ width }}>
        <button aria-label="调整抽屉宽度" className="absolute inset-y-0 -left-3 hidden w-6 cursor-col-resize place-items-center text-muted-foreground hover:text-foreground sm:grid" onPointerDown={startResize} type="button"><GripVertical className="size-4" /></button>
        <header className="border-b border-border px-5 py-4 sm:px-6">
          <div className="flex items-start justify-between gap-4">
            <div><div className="flex flex-wrap items-center gap-2"><Route className="size-5 text-primary" /><h2 className="text-lg font-semibold">文档处理链路</h2><StatusBadge status={status} /></div><p className="mt-2 break-all font-mono text-xs text-muted-foreground">{trace.id}</p></div>
            <button aria-label="关闭处理链路" className="rounded-md p-2 text-muted-foreground hover:bg-surface-muted hover:text-foreground" onClick={onClose} type="button"><X className="size-5" /></button>
          </div>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs"><span><span className="text-muted-foreground">总耗时</span> <strong className="ml-1 font-mono">{formatDuration(totalDuration)}</strong></span><span><span className="text-muted-foreground">阶段</span> <strong className="ml-1">{stages.length}</strong></span><span><span className="text-muted-foreground">尝试</span> <strong className="ml-1">{(trace.attempt ?? 0) + 1}</strong></span></div>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">
          {trace.error ? <div className="sticky top-0 z-10 mb-4 rounded-lg border border-danger/30 bg-danger/10 p-4 text-sm shadow-sm backdrop-blur"><div className="flex items-start gap-2"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" /><div><p className="font-semibold text-danger">处理失败</p><p className="mt-1 text-muted-foreground">{trace.error}</p></div></div></div> : null}
          <div className="mb-3 flex items-center justify-between text-xs text-muted-foreground"><span>阶段</span><span className="inline-flex items-center gap-1"><Clock3 className="size-3" />相对执行时间</span></div>
          {stages.length ? <div className="space-y-2">{stages.map((stage, index) => <WaterfallStage key={`${stage.name}-${stage.started_at}-${index}`} onSelect={() => setSelectedIndex(selectedIndex === index ? null : index)} selected={selectedIndex === index} stage={stage} totalDuration={totalDuration} traceStart={traceStart} />)}</div> : <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">任务已创建，但还没有产生阶段事件。</div>}
        </div>
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-4 sm:px-6"><button className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground hover:text-foreground" onClick={() => void navigator.clipboard.writeText(trace.id)} type="button"><Copy className="size-3.5" />复制 Trace ID</button><Link className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary" href={`/traces?type=ingestion&id=${trace.id}`}>打开全局 Trace <ArrowUpRight className="size-4" /></Link></footer>
      </aside>
    </div>
  );
}
