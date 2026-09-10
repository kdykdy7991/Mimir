"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, Boxes, CheckCircle2, Clock3, FileStack, FileText, ImageIcon, Layers3, MoreHorizontal, Route, Sparkles, Table2, Upload } from "lucide-react";
import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { useApiResource } from "@/api/use-api-resource";
import { Button, ErrorState, LoadingState, StatusBadge } from "@/components";
import type { CollectionDetail, DocumentDetail, TraceResponse } from "@/types";
import { DOCUMENT_ACCEPT, documentExtension, parsingMethod } from "./document-presentation";
import { traceStageDescription, traceStageLabel } from "../traces/trace-presentation";

type DetailData = { collection: CollectionDetail; document: DocumentDetail; trace?: TraceResponse };

function bytes(value: number) {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
function date(value: string) { return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function duration(value: number) { return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} 秒`; }

function InfoCard({ icon: Icon, label, value }: { icon: typeof Boxes; label: string; value: string }) {
  return <article className="glass-surface rounded-xl p-5"><div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-md bg-primary/10 text-primary"><Icon className="size-4" /></span><div><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 font-semibold">{value}</p></div></div></article>;
}

function MarkdownPreview({ source }: { source: string }) {
  const lines = source.split("\n");
  const nodes: React.ReactNode[] = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index] ?? "";
    if (/^#{1,4}\s/.test(line)) {
      const level = line.match(/^#+/)?.[0].length ?? 1;
      nodes.push(<div className={`${level <= 2 ? "mt-6 text-xl" : "mt-5 text-base"} font-semibold first:mt-0`} key={index}>{line.replace(/^#{1,4}\s+/, "")}</div>);
      index += 1;
      continue;
    }
    if (line.includes("|") && lines[index + 1]?.match(/^\s*\|?\s*:?-+/)) {
      const tableLines = [line];
      index += 2;
      while (index < lines.length && lines[index]?.includes("|")) tableLines.push(lines[index++] ?? "");
      const rows = tableLines.map((row) => row.split("|").map((cell) => cell.trim()).filter(Boolean));
      nodes.push(<div className="my-5 overflow-x-auto rounded-md border border-border" key={`table-${index}`}><table className="w-full text-left text-sm"><thead className="bg-primary/5"><tr>{rows[0]?.map((cell, cellIndex) => <th className="border-b border-border px-4 py-2.5 font-semibold" key={cellIndex}>{cell}</th>)}</tr></thead><tbody>{rows.slice(1).map((row, rowIndex) => <tr className="border-b border-border last:border-0" key={rowIndex}>{row.map((cell, cellIndex) => <td className="px-4 py-2.5" key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    if (line.trim()) nodes.push(<p className="my-3 text-sm leading-7" key={index}>{line}</p>);
    index += 1;
  }
  return <div>{nodes}</div>;
}

function OriginalFilePreview({ document }: { document: DocumentDetail }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [text, setText] = useState<string>();
  const [error, setError] = useState<string>();
  const ext = documentExtension(document.filename).toLowerCase();
  const url = apiClient.documentPreviewUrl(document.id);
  const isImage = ["png", "jpg", "jpeg", "gif", "webp", "bmp"].includes(ext);
  const isDirectPreview = ext === "pdf" || isImage;
  const [loading, setLoading] = useState(!isDirectPreview);

  useEffect(() => {
    const controller = new AbortController();
    if (isDirectPreview) return () => controller.abort();
    void (async () => {
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw new Error(response.status === 404 ? "原始文件尚未保存，请重新上传后预览。" : "无法读取原始文件");
        const blob = await response.blob();
        if (ext === "docx") {
          const { renderAsync } = await import("docx-preview");
          if (containerRef.current) {
            containerRef.current.innerHTML = "";
            await renderAsync(blob, containerRef.current, undefined, { inWrapper: true, breakPages: true, ignoreWidth: false, ignoreHeight: false, useBase64URL: true });
          }
        } else if (["md", "markdown", "txt", "csv", "html", "htm"].includes(ext)) {
          setText(await blob.text());
        } else {
          setError("该格式暂不支持浏览器内预览，可下载原始文件查看。");
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "无法读取原始文件");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [ext, isDirectPreview, url]);

  if (ext === "pdf") return <iframe className="h-[44rem] w-full rounded-lg border border-border bg-white" src={url} title={`${document.filename} 原文件预览`} />;
  if (isImage) return <div className="grid min-h-80 place-items-center rounded-lg border border-border bg-surface-muted/30 p-4">
    {/* eslint-disable-next-line @next/next/no-img-element */}
    <img alt={document.filename} className="max-h-[44rem] max-w-full object-contain" src={url} />
  </div>;
  return <div className="max-h-[44rem] overflow-auto rounded-lg border border-border bg-white p-5 text-slate-900"><div className="docx-original-preview" ref={containerRef} />{loading ? <p className="py-12 text-center text-sm text-slate-500">正在加载原始文件…</p> : null}{text !== undefined ? (ext === "md" || ext === "markdown" ? <MarkdownPreview source={text} /> : <pre className="whitespace-pre-wrap font-sans text-sm leading-7">{text}</pre>) : null}{error ? <div className="py-12 text-center"><FileText className="mx-auto size-7 text-slate-400" /><p className="mt-3 text-sm">{error}</p><a className="mt-4 inline-block text-sm font-semibold text-primary" href={url}>下载原始文件</a></div> : null}</div>;
}

export function DocumentDetailView({ id }: { id: string }) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string>();
  const load = useCallback(async (signal: AbortSignal): Promise<DetailData> => {
    const document = await apiClient.getDocument(id, signal);
    const [collection, trace] = await Promise.all([
      apiClient.getCollection(document.collection_id, signal),
      document.last_task_id ? apiClient.getIngestionTrace(document.last_task_id, signal).catch(() => undefined) : Promise.resolve(undefined),
    ]);
    return { collection, document, ...(trace ? { trace } : {}) };
  }, [id]);
  const { data, error, loading, retry } = useApiResource(load);

  async function reupload(file?: File) {
    if (!file || !data) return;
    setUploading(true); setUploadMessage(undefined);
    try {
      await apiClient.uploadDocument(data.collection.id, file);
      setUploadMessage("已提交重新解析，处理完成后刷新页面可查看最新结果。");
    } catch (reason) { setUploadMessage(reason instanceof Error ? reason.message : "重新上传失败"); }
    finally { setUploading(false); if (uploadRef.current) uploadRef.current.value = ""; }
  }

  if (loading) return <div className="app-container"><LoadingState label="正在加载文档详情" rows={6} /></div>;
  if (error || !data) {
    const apiError = error instanceof ApiError ? error : undefined;
    return <div className="app-container"><ErrorState {...(apiError?.code ? { code: apiError.code } : {})} {...(error instanceof Error ? { description: error.message } : {})} onRetry={retry} title="无法加载文档详情" /></div>;
  }

  const { collection, document, trace } = data;
  const method = document.parser_engine ?? parsingMethod(document.filename);
  const parseWarnings = document.parse_warnings ?? [];
  const chunks = document.chunks ?? [];
  const traceHref = document.last_task_id ? `/traces?type=ingestion&id=${document.last_task_id}` : document.last_query_id ? `/traces?type=query&id=${document.last_query_id}` : undefined;

  return <div className="app-container space-y-5">
    <nav aria-label="面包屑" className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><Link href="/collections">知识库</Link><span>/</span><Link href={`/collections/${collection.id}`}>{collection.name}</Link><span>/</span><span className="text-foreground">{document.filename}</span></nav>
    <header className="flex flex-col justify-between gap-5 sm:flex-row sm:items-center"><div><Link className="mb-3 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground" href={`/collections/${collection.id}`}><ArrowLeft className="size-3.5" />返回知识库</Link><div className="flex flex-wrap items-center gap-3"><span className="grid size-11 place-items-center rounded-lg bg-primary/10 text-primary"><FileText className="size-5" /></span><h1 className="text-3xl font-semibold tracking-[-0.035em]">{document.filename}</h1><StatusBadge status={document.status} />{parseWarnings.length ? <span className="inline-flex items-center gap-1 rounded-full border border-warning/30 bg-warning/10 px-2.5 py-1 text-xs font-semibold text-warning"><AlertTriangle className="size-3.5" />{parseWarnings.length} 条解析警告</span> : null}</div><p className="mt-3 text-sm text-muted-foreground">{bytes(document.size_bytes)} · 上传于 {date(document.created_at)}</p></div><div className="flex gap-2"><input accept={DOCUMENT_ACCEPT} className="sr-only" onChange={(event) => void reupload(event.target.files?.[0])} ref={uploadRef} type="file" /><Button loading={uploading} onClick={() => uploadRef.current?.click()}><Upload className="size-4" />重新上传</Button><Button aria-label="更多操作" variant="secondary"><MoreHorizontal className="size-4" /></Button></div></header>
    {uploadMessage ? <p className="rounded-md border border-info/20 bg-info/5 px-4 py-3 text-sm text-muted-foreground">{uploadMessage}</p> : null}
    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><InfoCard icon={Boxes} label="所属知识库" value={collection.name} /><InfoCard icon={FileStack} label="文档格式" value={`${documentExtension(document.filename)}${document.page_count ? ` · ${document.page_count} 页` : ""}`} /><InfoCard icon={Layers3} label="可检索片段" value={document.chunk_count.toLocaleString()} /><InfoCard icon={ImageIcon} label="提取图片" value={document.image_count.toLocaleString()} /></section>
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.7fr)_minmax(19rem,0.75fr)]"><section className="glass-surface rounded-xl p-5 sm:p-6"><div className="flex items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">原始文件预览</h2><p className="mt-1 text-sm text-muted-foreground">按原文件版式查看；检索分块不会用于重建这里的内容。</p></div>{document.page_count ? <span className="rounded-full bg-surface-muted px-3 py-1 text-xs font-medium">共 {document.page_count} 页</span> : <span className="rounded-full bg-surface-muted px-3 py-1 text-xs font-medium">保真预览</span>}</div><div className="mt-5"><OriginalFilePreview document={document} /></div></section>
      <div className="space-y-5"><section className="glass-surface rounded-xl p-5"><h2 className="text-lg font-semibold">处理信息</h2><dl className="mt-5 space-y-4 text-sm"><div className="flex justify-between gap-4"><dt className="text-muted-foreground">解析方式</dt><dd className="font-medium">{method}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">视觉处理</dt><dd className="font-medium">{document.vision_processed === true ? <span className="inline-flex items-center gap-1 text-accent"><Sparkles className="size-3.5" />已触发</span> : document.vision_processed === false ? "未触发（文本可用）" : "无可用记录"}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">表格识别</dt><dd className="font-medium">{document.table_count ?? 0} 个</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">图片处理</dt><dd className="font-medium">{document.image_count} 张</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">最近处理</dt><dd className="text-right font-medium">{date(document.updated_at)}</dd></div></dl></section>
      <section className="glass-surface rounded-xl p-5"><div className="flex items-center gap-2"><Route className="size-4 text-primary" /><h2 className="text-lg font-semibold">处理流程</h2></div>{trace?.stages?.length ? <ol className="mt-5 space-y-4">{trace.stages.map((stage, index) => { const description = traceStageDescription(stage); const skipped = stage.details?.event === "skipped"; return <li className="flex gap-3" key={`${stage.name}-${index}`}><span className={`grid size-6 shrink-0 place-items-center rounded-full text-primary-foreground ${skipped ? "bg-muted-foreground" : "bg-primary"}`}><CheckCircle2 className="size-3.5" /></span><div className="min-w-0 flex-1"><div className="flex justify-between gap-3"><p className="text-sm font-semibold">{traceStageLabel(stage)}</p><p className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Clock3 className="size-3" />{skipped ? "—" : duration(stage.duration_ms)}</p></div><p className="mt-0.5 text-xs text-muted-foreground">{description ?? date(stage.started_at)}</p></div></li>; })}</ol> : <p className="mt-4 text-sm text-muted-foreground">该文档没有可用的阶段 Trace。</p>}{traceHref ? <Link className="mt-5 inline-flex items-center gap-2 text-sm font-semibold text-primary" href={traceHref}>查看完整 Trace <span aria-hidden="true">→</span></Link> : null}</section></div></div>
    {parseWarnings.length ? <section className="flex items-start justify-between gap-4 rounded-xl border border-warning/30 bg-warning/5 p-4"><div className="flex gap-3"><AlertTriangle className="mt-0.5 size-5 shrink-0 text-warning" /><div><p className="font-semibold">检测到解析警告</p><ul className="mt-1 space-y-1 text-sm text-muted-foreground">{parseWarnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul><p className="mt-1 text-xs text-muted-foreground">警告不会隐藏已成功解析的内容，请结合预览进行核验。</p></div></div><span className="shrink-0 rounded-full border border-warning/30 px-2.5 py-1 text-xs text-warning">非阻断警告</span></section> : null}
    <section className="glass-surface rounded-xl p-5"><div className="flex items-center gap-2"><Table2 className="size-4 text-primary" /><h2 className="text-lg font-semibold">文档片段</h2><span className="text-xs text-muted-foreground">共 {chunks.length} 条</span></div><div className="mt-4 overflow-x-auto"><table className="w-full min-w-[42rem] text-left text-sm"><thead className="border-y border-border bg-surface-muted/60 text-xs text-muted-foreground"><tr><th className="px-4 py-3">序号</th><th className="px-4 py-3">章节</th><th className="px-4 py-3">页码</th><th className="px-4 py-3">字符数</th><th className="px-4 py-3">类型</th><th className="px-4 py-3">状态</th></tr></thead><tbody className="divide-y divide-border">{chunks.map((chunk) => <tr key={chunk.chunk_id}><td className="px-4 py-3 font-mono text-xs">{chunk.index + 1}</td><td className="max-w-md truncate px-4 py-3 font-medium">{chunk.heading ?? "未识别章节"}</td><td className="px-4 py-3">{chunk.page ?? "—"}</td><td className="px-4 py-3">{chunk.character_count.toLocaleString()}</td><td className="px-4 py-3">{chunk.content_type === "table" ? "表格" : chunk.content_type === "image_ocr" ? "图片 OCR" : chunk.content_type === "image_caption" ? "图片描述" : "正文"}</td><td className="px-4 py-3"><span className="inline-flex items-center gap-1 text-success"><CheckCircle2 className="size-3.5" />已解析</span></td></tr>)}</tbody></table>{!chunks.length ? <p className="py-8 text-center text-sm text-muted-foreground">暂无片段记录</p> : null}</div></section>
  </div>;
}
