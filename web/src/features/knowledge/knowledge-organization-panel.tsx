"use client";

import { useCallback, useState } from "react";
import { Folder, FolderPlus, Pencil, Tags, Trash2 } from "lucide-react";
import { apiClient } from "@/api";
import { useApiResource } from "@/api/use-api-resource";
import { Button, ErrorState, LoadingState } from "@/components";

const COLORS = ["blue", "green", "amber", "red", "purple", "gray"];

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
  const [folderName, setFolderName] = useState("");
  const [tagName, setTagName] = useState("");
  const [tagColor, setTagColor] = useState("blue");
  const [parentId, setParentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function createFolder() {
    if (!folderName.trim()) return;
    setBusy(true);
    try {
      await apiClient.createDocumentFolder(collectionId, {
        name: folderName.trim(),
        parent_id: parentId,
      });
      setFolderName("");
      retry();
    } finally {
      setBusy(false);
    }
  }
  async function createTag() {
    if (!tagName.trim()) return;
    setBusy(true);
    try {
      await apiClient.createDocumentTag(collectionId, {
        name: tagName.trim(),
        color: tagColor,
      });
      setTagName("");
      retry();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="grid gap-4 lg:grid-cols-2">
      <div className="glass-surface rounded-xl p-5">
        <div className="flex items-center gap-2">
          <Folder className="size-4 text-primary" />
          <h2 className="font-semibold">文件夹</h2>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          按目录组织文档；删除仅允许空目录。
        </p>
        <div className="mt-4 grid gap-2 sm:grid-cols-[minmax(0,1fr)_10rem_auto]">
          <input
            aria-label="新文件夹名称"
            className="h-9 min-w-0 rounded-md border border-border bg-surface px-3 text-sm"
            onChange={(event) => setFolderName(event.target.value)}
            placeholder="新文件夹"
            value={folderName}
          />
          <select
            aria-label="父文件夹"
            className="h-9 rounded-md border border-border bg-surface px-2 text-sm"
            onChange={(event) => setParentId(event.target.value || null)}
            value={parentId ?? ""}
          >
            <option value="">根目录</option>
            {data?.folders.map((folder) => (
              <option key={folder.id} value={folder.id}>
                {"　".repeat(folder.depth)}
                {folder.name}
              </option>
            ))}
          </select>
          <Button
            disabled={busy || !folderName.trim()}
            onClick={() => void createFolder()}
            variant="secondary"
          >
            <FolderPlus className="size-4" />
            新建
          </Button>
        </div>
        {loading ? (
          <div className="mt-4">
            <LoadingState label="加载文件夹" rows={2} />
          </div>
        ) : error ? (
          <div className="mt-4">
            <ErrorState onRetry={retry} title="无法加载文件夹" />
          </div>
        ) : (
          <div className="mt-4 space-y-1">
            <a
              className="block rounded-md px-3 py-2 text-sm hover:bg-primary/5"
              href={`?folder_id=root`}
            >
              全部根目录文档
            </a>
            {data?.folders.length ? (
              data.folders.map((item) => (
                <div
                  className="flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-2 text-sm"
                  key={item.id}
                  style={{ marginLeft: `${item.depth * 12}px` }}
                >
                  <a
                    className="min-w-0 flex-1 truncate hover:text-primary"
                    href={`?folder_id=${encodeURIComponent(item.id)}`}
                    title={item.name}
                  >
                    {item.name}
                  </a>
                  <span className="text-xs text-muted-foreground">
                    {item.document_count}
                  </span>
                  <select
                    aria-label={`移动文件夹 ${item.name}`}
                    className="h-7 max-w-28 rounded border border-border bg-surface px-1 text-xs"
                    onChange={async (event) => {
                      const target = event.target.value || null;
                      await apiClient.moveDocumentFolder(
                        collectionId,
                        item.id,
                        target,
                      );
                      retry();
                    }}
                    title="移动到目标文件夹"
                    value={item.parent_id ?? ""}
                  >
                    <option value="">根目录</option>
                    {data.folders
                      .filter((folder) => folder.id !== item.id)
                      .map((folder) => (
                        <option key={folder.id} value={folder.id}>
                          {"　".repeat(folder.depth)}
                          {folder.name}
                        </option>
                      ))}
                  </select>
                  <button
                    aria-label={`重命名文件夹 ${item.name}`}
                    className="text-muted-foreground hover:text-primary"
                    onClick={async () => {
                      const name = window.prompt("新文件夹名称", item.name);
                      if (name?.trim()) {
                        await apiClient.updateDocumentFolder(
                          collectionId,
                          item.id,
                          { name: name.trim() },
                        );
                        retry();
                      }
                    }}
                    title="重命名"
                    type="button"
                  >
                    <Pencil className="size-3.5" />
                  </button>
                  <button
                    aria-label={`删除文件夹 ${item.name}`}
                    className="text-muted-foreground hover:text-danger"
                    onClick={async () => {
                      await apiClient.deleteDocumentFolder(
                        collectionId,
                        item.id,
                      );
                      retry();
                    }}
                    title="删除空文件夹"
                    type="button"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">暂无文件夹</p>
            )}
          </div>
        )}
      </div>
      <div className="glass-surface rounded-xl p-5">
        <div className="flex items-center gap-2">
          <Tags className="size-4 text-accent" />
          <h2 className="font-semibold">标签</h2>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          创建知识库内复用的分类标签。
        </p>
        <div className="mt-4 flex gap-2">
          <input
            aria-label="新标签名称"
            className="h-9 min-w-0 flex-1 rounded-md border border-border bg-surface px-3 text-sm"
            onChange={(event) => setTagName(event.target.value)}
            placeholder="新标签"
            value={tagName}
          />
          <select
            aria-label="标签颜色"
            className="h-9 rounded-md border border-border bg-surface px-2 text-sm"
            onChange={(event) => setTagColor(event.target.value)}
            value={tagColor}
          >
            {COLORS.map((color) => (
              <option key={color} value={color}>
                {color}
              </option>
            ))}
          </select>
          <Button
            disabled={busy || !tagName.trim()}
            onClick={() => void createTag()}
            variant="secondary"
          >
            新建
          </Button>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {data?.tags.length ? (
            data.tags.map((item) => (
              <span
                className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-sm"
                key={item.id}
              >
                <a
                  className="max-w-40 truncate hover:text-primary"
                  href={`?tag_id=${encodeURIComponent(item.id)}`}
                  title={item.name}
                >
                  {item.name}
                </a>
                <span className="text-xs text-muted-foreground">
                  {item.document_count ?? 0}
                </span>
                <select
                  aria-label={`修改标签 ${item.name} 的颜色`}
                  className="h-7 rounded border border-border bg-surface px-1 text-xs"
                  onChange={async (event) => {
                    await apiClient.updateDocumentTag(collectionId, item.id, {
                      color: event.target.value,
                    });
                    retry();
                  }}
                  value={item.color}
                >
                  {COLORS.map((color) => (
                    <option key={color} value={color}>
                      {color}
                    </option>
                  ))}
                </select>
                <button
                  aria-label={`编辑标签 ${item.name}`}
                  className="text-muted-foreground hover:text-primary"
                  onClick={async () => {
                    const name = window.prompt("新标签名称", item.name);
                    if (name?.trim()) {
                      await apiClient.updateDocumentTag(collectionId, item.id, {
                        name: name.trim(),
                      });
                      retry();
                    }
                  }}
                  title="重命名"
                  type="button"
                >
                  <Pencil className="size-3.5" />
                </button>
                <button
                  aria-label={`删除标签 ${item.name}`}
                  className="text-muted-foreground hover:text-danger"
                  onClick={async () => {
                    if (
                      window.confirm(
                        `删除标签“${item.name}”？当前关联 ${item.document_count ?? 0} 份文档。`,
                      )
                    ) {
                      await apiClient.deleteDocumentTag(collectionId, item.id);
                      retry();
                    }
                  }}
                  title="删除标签"
                  type="button"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </span>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">暂无标签</p>
          )}
        </div>
      </div>
    </section>
  );
}
