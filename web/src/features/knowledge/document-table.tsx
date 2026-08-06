import { FileText, ImageIcon, Trash2 } from "lucide-react";
import { StatusBadge } from "@/components";
import type { DocumentSummary } from "@/types";

export type DocumentRow = DocumentSummary & { collectionName?: string };

function size(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function DocumentTable({ documents, onDelete }: { documents: DocumentRow[]; onDelete?: (document: DocumentRow) => void }) {
  return <div className="overflow-hidden rounded-lg border border-border bg-surface">
    <div className="hidden grid-cols-[minmax(16rem,1fr)_9rem_8rem_10rem_7rem_2.5rem] items-center gap-4 border-b border-border bg-surface-muted/65 px-5 py-3 text-xs font-semibold text-muted-foreground md:grid"><span>文档</span><span>状态</span><span>片段 / 图片</span><span>更新时间</span><span>大小</span><span className="sr-only">操作</span></div>
    <div className="divide-y divide-border">{documents.map((document) => <article className="grid gap-4 p-4 md:grid-cols-[minmax(16rem,1fr)_9rem_8rem_10rem_7rem_2.5rem] md:items-center md:px-5" key={document.id}>
      <div className="flex min-w-0 items-center gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-md bg-danger/10 text-danger"><FileText className="size-5" /></span><div className="min-w-0"><p className="truncate font-medium">{document.filename}</p>{document.collectionName ? <p className="mt-0.5 truncate text-xs text-muted-foreground">{document.collectionName}</p> : null}</div></div>
      <StatusBadge status={document.status} />
      <div className="flex items-center gap-3 text-xs text-muted-foreground"><span>{document.chunk_count ?? 0} 段</span><span className="inline-flex items-center gap-1"><ImageIcon className="size-3" />{document.image_count ?? 0}</span></div>
      <span className="text-xs text-muted-foreground">{new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(document.updated_at))}</span>
      <span className="text-xs text-muted-foreground">{size(document.size_bytes)}</span>
      {onDelete ? <button aria-label={`删除 ${document.filename}`} className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-danger/10 hover:text-danger" onClick={() => onDelete(document)} type="button"><Trash2 className="size-4" /></button> : null}
    </article>)}</div>
  </div>;
}
