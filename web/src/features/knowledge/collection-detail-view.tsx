"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import {
  ChevronRight,
  FileStack,
  Pencil,
  Sparkles,
  UploadCloud,
} from "lucide-react";

import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { useApiResource } from "@/api/use-api-resource";
import { Button, ErrorState, LoadingState } from "@/components";

import { DocumentsView } from "./documents-view";
import { EditCollectionDialog } from "./edit-collection-dialog";
import { KnowledgeOrganizationPanel } from "./knowledge-organization-panel";

export function CollectionDetailView({ id }: { id: string }) {
  const load = useCallback(
    (signal: AbortSignal) => apiClient.getCollection(id, signal),
    [id],
  );
  const { data, error, loading, retry } = useApiResource(load);
  const [editOpen, setEditOpen] = useState(false);
  const [uploadSignal, setUploadSignal] = useState(0);

  if (loading) {
    return (
      <div className="app-container">
        <LoadingState label="正在加载知识库详情" rows={6} />
      </div>
    );
  }
  if (error || !data) {
    const apiError = error instanceof ApiError ? error : undefined;
    return (
      <div className="app-container">
        <ErrorState
          {...(apiError
            ? {
                code: apiError.code,
                ...(apiError.requestId
                  ? { requestId: apiError.requestId }
                  : {}),
              }
            : {})}
          {...(error instanceof Error ? { description: error.message } : {})}
          onRetry={retry}
          title="无法加载知识库"
        />
      </div>
    );
  }

  const description = data.description?.trim() || "暂无描述";
  const updated = new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
  }).format(new Date(data.created_at ?? data.updated_at));
  const totalChunks = (data.chunk_count ?? 0).toLocaleString();

  return (
    <div className="app-container space-y-6">
      <nav
        aria-label="面包屑"
        className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground"
      >
        <Link className="hover:text-foreground" href="/collections">
          知识库
        </Link>
        <ChevronRight aria-hidden="true" className="size-3" />
        <span className="break-words text-foreground" title={data.name}>
          {data.name}
        </span>
      </nav>
      <header className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
              <FileStack aria-hidden="true" className="size-5" />
            </span>
            <h1
              className="break-words text-3xl font-semibold tracking-[-0.035em] sm:text-4xl"
              title={data.name}
            >
              {data.name}
            </h1>
            <span className="rounded-full border border-success/20 bg-success/10 px-2.5 py-0.5 text-[0.6875rem] text-success">
              实时 API
            </span>
          </div>
          <p
            className="mt-3 max-w-3xl text-muted-foreground"
            style={{
              display: "-webkit-box",
              WebkitBoxOrient: "vertical",
              WebkitLineClamp: 3,
              overflow: "hidden",
            }}
            title={description}
          >
            {description}
          </p>
          <dl className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
            <div className="flex items-center gap-1">
              <dt>创建于</dt>
              <dd className="font-medium text-foreground">{updated}</dd>
            </div>
            <div className="flex items-center gap-1">
              <dt>·</dt>
              <dt>{data.document_count ?? 0} 份文档</dt>
            </div>
            <div className="flex items-center gap-1">
              <dt>·</dt>
              <dt>{totalChunks} 个片段</dt>
            </div>
          </dl>
        </div>
        <div className="flex flex-wrap items-center gap-2 lg:flex-shrink-0">
          <Button onClick={() => setEditOpen(true)} variant="secondary">
            <Pencil aria-hidden="true" className="size-4" />
            编辑知识库
          </Button>
          <Button onClick={() => setUploadSignal((count) => count + 1)}>
            <UploadCloud aria-hidden="true" className="size-4" />
            上传文档
          </Button>
        </div>
      </header>
      <section
        aria-label="知识库统计"
        className="grid gap-4 sm:grid-cols-3"
      >
        <div className="glass-surface rounded-lg p-5">
          <FileStack aria-hidden="true" className="size-5 text-primary" />
          <p className="mt-4 text-2xl font-semibold">
            {data.document_count ?? 0}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">文档总数</p>
        </div>
        <div className="glass-surface rounded-lg p-5">
          <Sparkles aria-hidden="true" className="size-5 text-accent" />
          <p className="mt-4 text-2xl font-semibold">{totalChunks}</p>
          <p className="mt-1 text-xs text-muted-foreground">可检索片段</p>
        </div>
        <div className="glass-surface rounded-lg p-5">
          <UploadCloud aria-hidden="true" className="size-5 text-success" />
          <p className="mt-4 text-sm font-semibold">
            {new Intl.DateTimeFormat("zh-CN", {
              dateStyle: "medium",
              timeStyle: "short",
            }).format(new Date(data.updated_at))}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">最后更新</p>
        </div>
      </section>
      <KnowledgeOrganizationPanel collectionId={id} />
      <DocumentsView
        collectionId={id}
        collection={data}
        embedded
        openUploadSignal={uploadSignal}
      />
      <EditCollectionDialog
        collection={data}
        onOpenChange={setEditOpen}
        onUpdated={() => {
          setEditOpen(false);
          retry();
        }}
        open={editOpen}
      />
    </div>
  );
}
