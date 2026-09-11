"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { ChevronRight, Folder, FolderPlus, Pencil, Plus, Tag as TagIcon, Trash2 } from "lucide-react";

import { apiClient } from "@/api";
import { useApiResource } from "@/api/use-api-resource";
import { Button, ErrorState, LoadingState } from "@/components";
import type { DocumentFolder, DocumentTag } from "@/types";
import { tagDotClass, tagPillClass } from "./tag-color";
import {
  FolderCreateDialog,
  FolderDeleteDialog,
  FolderRenameDialog,
  TagDialog,
} from "./org-dialogs";

export function KnowledgeOrganizationPanel({
  collectionId,
}: {
  collectionId: string;
}) {
  const load = useCallback(
    async (signal: AbortSignal) => {
      const [folders, tags] = await Promise.all([
        apiClient.listDocumentFolders(collectionId, signal),
        apiClient.listDocumentTags(collectionId, signal),
      ]);
      return { folders, tags };
    },
    [collectionId],
  );
  const { data, error, loading, retry } = useApiResource(load);
  const [folderDialog, setFolderDialog] = useState<
    | { kind: "create" }
    | { kind: "rename"; folder: DocumentFolder }
    | { kind: "delete"; folder: DocumentFolder }
    | null
  >(null);
  const [tagDialog, setTagDialog] = useState<
    | { kind: "create" }
    | { kind: "edit"; tag: DocumentTag }
    | null
  >(null);

  const renameFolder =
    folderDialog?.kind === "rename" ? folderDialog.folder : null;
  const deleteFolder =
    folderDialog?.kind === "delete" ? folderDialog.folder : null;
  const editTag = tagDialog?.kind === "edit" ? tagDialog.tag : null;

  return (
    <section className="grid gap-4 lg:grid-cols-2">
      <FoldersCard
        collectionId={collectionId}
        {...(data ? { data } : {})}
        error={error}
        loading={loading}
        onCreate={() => setFolderDialog({ kind: "create" })}
        onDelete={(folder) => setFolderDialog({ kind: "delete", folder })}
        onRename={(folder) => setFolderDialog({ kind: "rename", folder })}
        retry={retry}
      />
      <TagsCard
        collectionId={collectionId}
        {...(data ? { data } : {})}
        error={error}
        loading={loading}
        onCreate={() => setTagDialog({ kind: "create" })}
        onEdit={(tag) => setTagDialog({ kind: "edit", tag })}
        retry={retry}
      />
      <FolderCreateDialog
        collectionId={collectionId}
        folders={data?.folders ?? []}
        onClose={() => setFolderDialog(null)}
        onSaved={retry}
        open={folderDialog?.kind === "create"}
      />
      {renameFolder ? (
        <FolderRenameDialog
          collectionId={collectionId}
          folder={renameFolder}
          folders={data?.folders ?? []}
          onClose={() => setFolderDialog(null)}
          onSaved={retry}
          open={folderDialog?.kind === "rename"}
        />
      ) : null}
      {deleteFolder ? (
        <FolderDeleteDialog
          collectionId={collectionId}
          folder={deleteFolder}
          onClose={() => setFolderDialog(null)}
          onDeleted={retry}
          open={folderDialog?.kind === "delete"}
        />
      ) : null}
      <TagDialog
        collectionId={collectionId}
        mode={editTag ? "edit" : "create"}
        onClose={() => setTagDialog(null)}
        onSaved={retry}
        open={tagDialog !== null}
        {...(editTag ? { tag: editTag } : {})}
        tags={data?.tags ?? []}
      />
    </section>
  );
}

type OrgData = { folders: DocumentFolder[]; tags: DocumentTag[] };

function FoldersCard({
  collectionId,
  data,
  error,
  loading,
  onCreate,
  onDelete,
  onRename,
  retry,
}: {
  collectionId: string;
  data?: OrgData;
  error?: unknown;
  loading: boolean;
  onCreate: () => void;
  onDelete: (folder: DocumentFolder) => void;
  onRename: (folder: DocumentFolder) => void;
  retry: () => void;
}) {
  const folders = data?.folders ?? [];
  return (
    <article className="glass-surface rounded-xl p-5">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Folder aria-hidden="true" className="size-4 text-primary" />
          <h2 className="font-semibold">文件夹</h2>
        </div>
        <Button onClick={onCreate} size="sm" variant="secondary">
          <FolderPlus aria-hidden="true" className="size-4" />
          新建文件夹
        </Button>
      </header>
      <p className="mt-1 text-xs text-muted-foreground">
        按目录组织文档；删除仅允许空目录。
      </p>
      {loading ? (
        <div className="mt-4">
          <LoadingState label="加载文件夹" rows={2} />
        </div>
      ) : error ? (
        <div className="mt-4">
          <ErrorState onRetry={retry} title="无法加载文件夹" />
        </div>
      ) : (
        <ul aria-label="文件夹列表" className="mt-4 space-y-1">
          <li>
            <Link
              aria-label="查看根目录下的全部文档"
              className="group flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-primary/5"
              href={`/collections/${collectionId}?folder_id=root`}
            >
              <ChevronRight
                aria-hidden="true"
                className="size-3.5 text-muted-foreground transition-transform group-hover:translate-x-0.5"
              />
              <span className="min-w-0 flex-1 truncate font-medium">
                全部根目录文档
              </span>
              <span className="text-xs text-muted-foreground">
                {folders.reduce((sum, item) => sum + item.document_count, 0)}
              </span>
            </Link>
          </li>
          {folders.length ? (
            folders.map((folder) => (
              <FolderRow
                collectionId={collectionId}
                folder={folder}
                key={folder.id}
                onDelete={onDelete}
                onRename={onRename}
              />
            ))
          ) : (
            <li>
              <p className="px-3 py-2 text-sm text-muted-foreground">
                暂无文件夹
              </p>
            </li>
          )}
        </ul>
      )}
    </article>
  );
}

function FolderRow({
  collectionId,
  folder,
  onDelete,
  onRename,
}: {
  collectionId: string;
  folder: DocumentFolder;
  onDelete: (folder: DocumentFolder) => void;
  onRename: (folder: DocumentFolder) => void;
}) {
  return (
    <li>
      <div
        className="group flex items-center gap-2 rounded-md border border-transparent px-3 py-2 text-sm hover:border-border hover:bg-surface"
        style={{ paddingLeft: `${12 + folder.depth * 16}px` }}
        title={folder.name}
      >
        <Folder aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
        <Link
          aria-label={`查看文件夹 ${folder.name} 下的内容`}
          className="min-w-0 flex-1 truncate hover:text-primary"
          href={`/collections/${collectionId}?folder_id=${encodeURIComponent(folder.id)}`}
        >
          {folder.name}
        </Link>
        <span className="text-xs text-muted-foreground">{folder.document_count}</span>
        <span className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <button
            aria-label={`重命名文件夹 ${folder.name}`}
            className="grid size-7 place-items-center rounded text-muted-foreground hover:bg-primary/10 hover:text-primary"
            onClick={() => onRename(folder)}
            title="重命名"
            type="button"
          >
            <Pencil aria-hidden="true" className="size-3.5" />
          </button>
          <button
            aria-label={`删除文件夹 ${folder.name}`}
            className="grid size-7 place-items-center rounded text-muted-foreground hover:bg-danger/10 hover:text-danger"
            onClick={() => onDelete(folder)}
            type="button"
          >
            <Trash2 aria-hidden="true" className="size-3.5" />
          </button>
        </span>
      </div>
    </li>
  );
}

function TagsCard({
  collectionId,
  data,
  error,
  loading,
  onCreate,
  onEdit,
  retry,
}: {
  collectionId: string;
  data?: OrgData;
  error?: unknown;
  loading: boolean;
  onCreate: () => void;
  onEdit: (tag: DocumentTag) => void;
  retry: () => void;
}) {
  const tags = data?.tags ?? [];
  return (
    <article className="glass-surface rounded-xl p-5">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <TagIcon aria-hidden="true" className="size-4 text-accent" />
          <h2 className="font-semibold">标签</h2>
        </div>
        <Button onClick={onCreate} size="sm" variant="secondary">
          <Plus aria-hidden="true" className="size-4" />
          新建标签
        </Button>
      </header>
      <p className="mt-1 text-xs text-muted-foreground">
        创建知识库内复用的分类标签。
      </p>
      {loading ? (
        <div className="mt-4">
          <LoadingState label="加载标签" rows={2} />
        </div>
      ) : error ? (
        <div className="mt-4">
          <ErrorState onRetry={retry} title="无法加载标签" />
        </div>
      ) : tags.length ? (
        <ul
          aria-label="标签列表"
          className="mt-4 flex flex-wrap gap-2"
          data-collection-id={collectionId}
        >
          {tags.map((tag) => (
            <TagChip key={tag.id} onEdit={onEdit} tag={tag} />
          ))}
        </ul>
      ) : (
        <p className="mt-4 px-3 py-2 text-sm text-muted-foreground">暂无标签</p>
      )}
    </article>
  );
}

function TagChip({
  tag,
  onEdit,
}: {
  tag: DocumentTag;
  onEdit: (tag: DocumentTag) => void;
}) {
  return (
    <li className="group inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-surface pl-3 pr-1.5 py-1.5 text-sm">
      <span
        aria-hidden="true"
        className={`size-2.5 shrink-0 rounded-full ${tagDotClass(tag.color)}`}
      />
      <Link
        className="max-w-32 truncate hover:text-primary"
        href={`?tag_id=${encodeURIComponent(tag.id)}`}
        title={tag.name}
      >
        {tag.name}
      </Link>
      <span
        className={`inline-flex h-5 items-center rounded-full px-1.5 text-[0.6875rem] font-semibold ${tagPillClass(tag.color)}`}
        title="关联文档数"
      >
        {tag.document_count ?? 0}
      </span>
      <span className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <button
          aria-label={`编辑标签 ${tag.name}`}
          className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-primary/10 hover:text-primary"
          onClick={() => onEdit(tag)}
          title="编辑标签"
          type="button"
        >
          <Pencil aria-hidden="true" className="size-3.5" />
        </button>
      </span>
    </li>
  );
}