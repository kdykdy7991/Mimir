"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Copy, FileSearch, LocateFixed, X } from "lucide-react";
import { Button, ErrorState, LoadingState } from "@/components";
import type { DocumentChunkDetail } from "@/types";

type Props = {
  chunk?: DocumentChunkDetail | undefined;
  error?: unknown;
  loading?: boolean;
  onClose: () => void;
  onNavigate: (chunkId: string) => void;
  onRetry: () => void;
  onShowSource: (chunk: DocumentChunkDetail) => void;
  open: boolean;
};

const TYPE_LABELS: Record<DocumentChunkDetail["content_type"], string> = {
  text: "正文",
  table: "表格",
  image_ocr: "图片 OCR",
  image_caption: "图片描述",
};

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value);
  const field = document.createElement("textarea");
  field.value = value;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.appendChild(field);
  field.select();
  const copied = document.execCommand("copy");
  field.remove();
  if (!copied) throw new Error("copy failed");
}

export function ChunkDetailDrawer({ chunk, error, loading = false, onClose, onNavigate, onRetry, onShowSource, open }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "text" | "id" | "failed">("idle");

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      previouslyFocused.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      dialog.showModal();
      return;
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function closed() {
    setCopyState("idle");
    previouslyFocused.current?.focus();
    onClose();
  }

  function navigate(chunkId: string | null) {
    if (!chunkId) return;
    setCopyState("idle");
    onNavigate(chunkId);
  }

  async function copy(kind: "text" | "id") {
    if (!chunk) return;
    try {
      await copyText(kind === "text" ? chunk.text : chunk.chunk_id);
      setCopyState(kind);
    } catch {
      setCopyState("failed");
    }
  }

  return <dialog aria-label="Chunk 详情" className="m-0 ml-auto h-dvh max-h-none w-full max-w-xl border-0 border-l border-border bg-surface-raised p-0 text-foreground shadow-2xl backdrop:bg-foreground/25 sm:w-[min(90vw,38rem)]" onCancel={(event) => { event.preventDefault(); dialogRef.current?.close(); }} onClose={closed} ref={dialogRef}>
    <div className="flex min-h-full flex-col">
      <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4 sm:px-6">
        <div className="min-w-0"><p className="font-mono text-[0.6875rem] font-semibold tracking-[0.12em] text-primary uppercase">Chunk inspector</p><h2 className="mt-1 text-lg font-semibold">文档片段详情</h2>{chunk ? <p className="mt-1 truncate text-xs text-muted-foreground">第 {chunk.index + 1} 段 · {TYPE_LABELS[chunk.content_type]}</p> : null}</div>
        <button aria-label="关闭 Chunk 详情" className="grid size-9 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-surface-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary" onClick={() => dialogRef.current?.close()} title="关闭" type="button"><X className="size-4" /></button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">
        {loading ? <LoadingState label="正在读取 Chunk 全文" rows={5} /> : error ? <ErrorState description={error instanceof Error ? error.message : "无法读取 Chunk 详情"} onRetry={onRetry} title="Chunk 加载失败" /> : chunk ? <div className="space-y-5">
          <dl className="grid grid-cols-2 gap-3 rounded-lg border border-border bg-surface-muted/40 p-4 text-sm">
            <div><dt className="text-xs text-muted-foreground">章节</dt><dd className="mt-1 break-words font-medium">{chunk.heading || "未识别章节"}</dd></div>
            <div><dt className="text-xs text-muted-foreground">页码</dt><dd className="mt-1 font-medium">{chunk.page ?? "—"}</dd></div>
            <div><dt className="text-xs text-muted-foreground">类型</dt><dd className="mt-1 font-medium">{TYPE_LABELS[chunk.content_type]}</dd></div>
            <div><dt className="text-xs text-muted-foreground">字符数</dt><dd className="mt-1 font-medium">{chunk.character_count.toLocaleString()}</dd></div>
          </dl>
          <section><div className="flex items-center justify-between gap-3"><h3 className="font-semibold">完整内容</h3><Button onClick={() => void copy("text")} size="sm" variant="ghost"><Copy className="size-3.5" />复制正文</Button></div><pre className="mt-3 whitespace-pre-wrap break-words rounded-lg border border-border bg-surface p-4 font-sans text-sm leading-7 selection:bg-primary/20">{chunk.text}</pre></section>
          <section><h3 className="text-xs font-semibold text-muted-foreground">Chunk ID</h3><div className="mt-2 flex items-center gap-2 rounded-md bg-surface-muted px-3 py-2"><code className="min-w-0 flex-1 break-all text-xs">{chunk.chunk_id}</code><button aria-label="复制 Chunk ID" className="grid size-8 shrink-0 place-items-center rounded text-muted-foreground hover:bg-surface hover:text-foreground" onClick={() => void copy("id")} title="复制 Chunk ID" type="button"><Copy className="size-3.5" /></button></div></section>
          {copyState === "text" ? <p aria-live="polite" className="text-sm text-success">Chunk 正文已复制。</p> : copyState === "id" ? <p aria-live="polite" className="text-sm text-success">Chunk ID 已复制。</p> : copyState === "failed" ? <p aria-live="polite" className="text-sm text-danger">浏览器阻止了复制，请手动选择内容。</p> : null}
        </div> : <div className="grid min-h-64 place-items-center text-center"><div><FileSearch className="mx-auto size-7 text-muted-foreground" /><p className="mt-3 text-sm text-muted-foreground">请选择一个文档片段。</p></div></div>}
      </div>

      {chunk ? <footer className="border-t border-border bg-surface px-5 py-4 sm:px-6"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex gap-2"><Button disabled={!chunk.previous_chunk_id} onClick={() => navigate(chunk.previous_chunk_id)} size="sm" variant="secondary"><ChevronLeft className="size-4" />上一段</Button><Button disabled={!chunk.next_chunk_id} onClick={() => navigate(chunk.next_chunk_id)} size="sm" variant="secondary">下一段<ChevronRight className="size-4" /></Button></div><Button onClick={() => onShowSource(chunk)} size="sm"><LocateFixed className="size-4" />在原文中查看</Button></div></footer> : null}
    </div>
  </dialog>;
}
