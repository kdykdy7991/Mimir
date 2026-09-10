import { FileText, ImageIcon, MoreHorizontal, Sparkles, Trash2 } from "lucide-react";
import Link from "next/link";
import { StatusBadge } from "@/components";
import type { DocumentSummary } from "@/types";
import { documentExtension, parsingMethod } from "./document-presentation";

export type DocumentRow = DocumentSummary & { collectionName?: string };

function size(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function DocumentTable({ documents, onDelete }: { documents: DocumentRow[]; onDelete?: (document: DocumentRow) => void }) {
  return <div className="overflow-hidden rounded-lg border border-border bg-surface">
    <div className="hidden grid-cols-[minmax(14rem,1fr)_5rem_7rem_8rem_7rem_10rem_2.5rem] items-center gap-4 border-b border-border bg-surface-muted/65 px-5 py-3 text-xs font-semibold text-muted-foreground lg:grid"><span>文档</span><span>格式</span><span>解析方式</span><span>状态</span><span>片段 / 图片</span><span>更新时间</span><span className="sr-only">操作</span></div>
    <div className="divide-y divide-border">{documents.map((document) => <article className="grid gap-4 p-4 lg:grid-cols-[minmax(14rem,1fr)_5rem_7rem_8rem_7rem_10rem_2.5rem] lg:items-center lg:px-5" key={`${document.collection_id}:${document.id}`}>
      <div className="flex min-w-0 items-center gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-md bg-primary/10 text-primary"><FileText className="size-5" /></span><div className="min-w-0"><Link className="truncate font-medium hover:text-primary" href={`/documents/${document.id}`}>{document.filename}</Link>{document.collectionName ? <p className="mt-0.5 truncate text-xs text-muted-foreground">{document.collectionName}</p> : null}</div></div>
      <span className="text-xs font-semibold text-muted-foreground">{documentExtension(document.filename)}</span>
      <span className={`inline-flex items-center gap-1.5 text-xs ${parsingMethod(document.filename) === "视觉解析" ? "text-accent" : "text-muted-foreground"}`}>{parsingMethod(document.filename) === "视觉解析" ? <Sparkles className="size-3.5" /> : null}{parsingMethod(document.filename)}</span>
      <StatusBadge status={document.status} />
      <div className="flex items-center gap-3 text-xs text-muted-foreground"><span>{document.chunk_count ?? 0} 段</span><span className="inline-flex items-center gap-1"><ImageIcon className="size-3" />{document.image_count ?? 0}</span></div>
      <span className="text-xs text-muted-foreground">{new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(document.updated_at))}</span>
      {onDelete ? <button aria-label={`删除 ${document.filename}`} className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-danger/10 hover:text-danger" onClick={() => onDelete(document)} title={`删除 · ${size(document.size_bytes)}`} type="button"><Trash2 className="size-4" /></button> : <MoreHorizontal className="size-4 text-muted-foreground" />}
    </article>)}</div>
  </div>;
}
