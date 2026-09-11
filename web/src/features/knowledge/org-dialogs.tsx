"use client";

import {
  FormEvent,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { ChevronDown, Plus, Trash2, X } from "lucide-react";

import { apiClient } from "@/api";
import { ApiError } from "@/api/error";
import { Button, ConfirmDialog } from "@/components";
import type { DocumentFolder, DocumentTag } from "@/types";
import { TAG_COLORS, isTagColor, tagPillClass, type TagColor } from "./tag-color";

type FolderDialogProps = {
  collectionId: string;
  folder: DocumentFolder;
  folders: DocumentFolder[];
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
};

type FolderCreateProps = {
  collectionId: string;
  folders: DocumentFolder[];
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
};

function Dialog({
  children,
  onClose,
  open,
}: {
  children: React.ReactNode;
  onClose: () => void;
  open: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      className="m-auto w-[min(calc(100%-2rem),32rem)] rounded-xl border border-border bg-surface-raised p-0 text-foreground shadow-lg backdrop:bg-foreground/20"
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
      ref={ref}
    >
      {children}
    </dialog>
  );
}

function collectDescendantIds(
  folders: DocumentFolder[],
  rootId: string,
): Set<string> {
  const ids = new Set<string>([rootId]);
  let added = true;
  while (added) {
    added = false;
    for (const folder of folders) {
      if (
        folder.parent_id &&
        ids.has(folder.parent_id) &&
        !ids.has(folder.id)
      ) {
        ids.add(folder.id);
        added = true;
      }
    }
  }
  return ids;
}

export { collectDescendantIds };

export function FolderCreateDialog({
  collectionId,
  folders,
  open,
  onClose,
  onSaved,
}: FolderCreateProps) {
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState<string>("");
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (open) {
      // Resetting form state on open is intentional; using a derived
      // initialiser would not pick up subsequent prop changes after the
      // first open. Suppress the cascading-render warning for this case.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setName("");
      setParentId("");
      setError(undefined);
      setPending(false);
    }
  }, [open]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) return;
    setPending(true);
    setError(undefined);
    try {
      await apiClient.createDocumentFolder(collectionId, {
        name: name.trim(),
        parent_id: parentId || null,
      });
      onSaved();
      onClose();
    } catch (reason) {
      setError(messageFor(reason, "创建失败"));
    } finally {
      setPending(false);
    }
  }
  return (
    <Dialog onClose={onClose} open={open}>
      <form onSubmit={submit}>
        <header className="flex items-start justify-between border-b border-border p-5">
          <div>
            <h2 className="text-base font-semibold">新建文件夹</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              用于按主题或阶段把文档分组管理。
            </p>
          </div>
          <button
            aria-label="关闭"
            className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-surface-muted"
            onClick={onClose}
            type="button"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        </header>
        <div className="space-y-4 p-5">
          <label className="block text-sm font-medium">
            文件夹名称
            <input
              autoFocus
              className="mt-2 h-9 w-full rounded-md border border-border bg-surface px-3 text-sm outline-none focus:border-primary"
              maxLength={128}
              onChange={(event) => setName(event.target.value)}
              required
              value={name}
            />
          </label>
          <FolderSelect
            label="父文件夹"
            onChange={setParentId}
            options={folders}
            value={parentId}
          />
          {error ? <p className="text-xs text-danger">{error}</p> : null}
        </div>
        <footer className="flex justify-end gap-2 border-t border-border bg-surface-muted/50 px-5 py-4">
          <Button onClick={onClose} type="button" variant="ghost">
            取消
          </Button>
          <Button loading={pending} type="submit">
            <Plus aria-hidden="true" className="size-4" />
            创建
          </Button>
        </footer>
      </form>
    </Dialog>
  );
}

export function FolderRenameDialog({
  collectionId,
  folder,
  folders,
  open,
  onClose,
  onSaved,
}: FolderDialogProps) {
  const [name, setName] = useState(folder.name);
  const [parentId, setParentId] = useState<string>(folder.parent_id ?? "");
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setName(folder.name);
      setParentId(folder.parent_id ?? "");
      setError(undefined);
      setPending(false);
    }
  }, [folder, open]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setPending(true);
    setError(undefined);
    try {
      const updates: { name?: string; parent_id?: string | null } = {};
      if (trimmed !== folder.name) updates.name = trimmed;
      const nextParent = parentId || null;
      if (nextParent !== folder.parent_id) updates.parent_id = nextParent;
      if (Object.keys(updates).length === 0) {
        onSaved();
        onClose();
        return;
      }
      await apiClient.updateDocumentFolder(collectionId, folder.id, updates);
      onSaved();
      onClose();
    } catch (reason) {
      setError(messageFor(reason, "重命名失败"));
    } finally {
      setPending(false);
    }
  }
  return (
    <Dialog onClose={onClose} open={open}>
      <form onSubmit={submit}>
        <header className="flex items-start justify-between border-b border-border p-5">
          <div>
            <h2 className="text-base font-semibold">编辑文件夹</h2>
            <p className="mt-1 truncate text-xs text-muted-foreground">
              {folder.name}
            </p>
          </div>
          <button
            aria-label="关闭"
            className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-surface-muted"
            onClick={onClose}
            type="button"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        </header>
        <div className="space-y-4 p-5">
          <label className="block text-sm font-medium">
            文件夹名称
            <input
              autoFocus
              className="mt-2 h-9 w-full rounded-md border border-border bg-surface px-3 text-sm outline-none focus:border-primary"
              maxLength={128}
              onChange={(event) => setName(event.target.value)}
              required
              value={name}
            />
          </label>
          <FolderSelect
            label="父文件夹"
            onChange={setParentId}
            options={folders.filter((item) => item.id !== folder.id)}
            value={parentId}
          />
          {error ? <p className="text-xs text-danger">{error}</p> : null}
        </div>
        <footer className="flex justify-end gap-2 border-t border-border bg-surface-muted/50 px-5 py-4">
          <Button onClick={onClose} type="button" variant="ghost">
            取消
          </Button>
          <Button loading={pending} type="submit">
            保存
          </Button>
        </footer>
      </form>
    </Dialog>
  );
}

export function FolderDeleteDialog({
  collectionId,
  folder,
  open,
  onClose,
  onDeleted,
}: Omit<FolderDialogProps, "folders" | "onSaved"> & { onDeleted: () => void }) {
  const description =
    folder.document_count > 0
      ? `该文件夹下还有 ${folder.document_count} 份文档，无法直接删除。请先把文档移到其他位置或删除文档。`
      : `即将永久删除文件夹“${folder.name}”，删除后无法恢复。`;
  return (
    <ConfirmDialog
      cancelLabel="取消"
      confirmLabel={folder.document_count > 0 ? "知道了" : "删除"}
      description={description}
      destructive={folder.document_count === 0}
      onConfirm={async () => {
        if (folder.document_count > 0) {
          onClose();
          return;
        }
        await apiClient.deleteDocumentFolder(collectionId, folder.id);
        onDeleted();
      }}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      open={open}
      title={`删除文件夹“${folder.name}”？`}
    />
  );
}

function FolderSelect({
  label,
  onChange,
  options,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  options: DocumentFolder[];
  value: string;
}) {
  const id = useId();
  const disabled = options.length === 0;
  return (
    <div className="text-sm font-medium">
      <span id={id}>{label}</span>
      <div className="relative mt-2">
        <select
          aria-labelledby={id}
          className="h-9 w-full appearance-none rounded-md border border-border bg-surface px-3 pr-9 text-sm outline-none focus:border-primary disabled:opacity-60"
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          value={value}
        >
          <option value="">根目录</option>
          {options.map((folder) => (
            <option
              key={folder.id}
              style={{ paddingLeft: `${folder.depth * 12}px` }}
              value={folder.id}
            >
              {folder.name}
            </option>
          ))}
        </select>
        <ChevronDown
          aria-hidden="true"
          className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        />
      </div>
    </div>
  );
}

type TagDialogProps = {
  collectionId: string;
  mode: "create" | "edit";
  tags: DocumentTag[];
  tag?: DocumentTag;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
};

export function TagDialog({
  collectionId,
  mode,
  tags,
  tag,
  open,
  onClose,
  onSaved,
}: TagDialogProps) {
  const [name, setName] = useState(tag?.name ?? "");
  const [color, setColor] = useState<TagColor>(
    isTagColor(tag?.color) ? tag!.color : "blue",
  );
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setName(tag?.name ?? "");
      setColor(isTagColor(tag?.color) ? (tag!.color as TagColor) : "blue");
      setError(undefined);
      setPending(false);
      setConfirmDelete(false);
    }
  }, [open, tag]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setPending(true);
    setError(undefined);
    try {
      if (mode === "create") {
        await apiClient.createDocumentTag(collectionId, {
          name: trimmed,
          color,
        });
      } else if (tag) {
        const updates: { name?: string; color?: string } = {};
        if (trimmed !== tag.name) updates.name = trimmed;
        if (color !== tag.color) updates.color = color;
        if (Object.keys(updates).length === 0) {
          onSaved();
          onClose();
          return;
        }
        await apiClient.updateDocumentTag(collectionId, tag.id, updates);
      }
      onSaved();
      onClose();
    } catch (reason) {
      setError(messageFor(reason, mode === "create" ? "创建失败" : "保存失败"));
    } finally {
      setPending(false);
    }
  }
  const duplicateName = useMemo(() => {
    if (!name.trim()) return false;
    const normalized = name.trim().toLowerCase();
    return tags.some(
      (item) =>
        item.id !== tag?.id &&
        item.name.trim().toLowerCase() === normalized,
    );
  }, [name, tag, tags]);
  return (
    <Dialog onClose={onClose} open={open}>
      <form onSubmit={submit}>
        <header className="flex items-start justify-between border-b border-border p-5">
          <div>
            <h2 className="text-base font-semibold">
              {mode === "create" ? "新建标签" : "编辑标签"}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              标签可在文档列表中作为筛选条件或分类标记。
            </p>
          </div>
          <button
            aria-label="关闭"
            className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-surface-muted"
            onClick={onClose}
            type="button"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        </header>
        <div className="space-y-4 p-5">
          <label className="block text-sm font-medium">
            标签名称
            <input
              autoFocus
              className="mt-2 h-9 w-full rounded-md border border-border bg-surface px-3 text-sm outline-none focus:border-primary"
              maxLength={64}
              onChange={(event) => setName(event.target.value)}
              required
              value={name}
            />
          </label>
          {duplicateName ? (
            <p className="text-xs text-warning">
              已存在同名标签，请使用不同名称。
            </p>
          ) : null}
          <fieldset>
            <legend className="text-sm font-medium">颜色</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {TAG_COLORS.map((token) => {
                const active = token === color;
                return (
                  <button
                    aria-pressed={active}
                    aria-label={`选择颜色 ${token}`}
                    className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors ${tagPillClass(token)} ${
                      active
                        ? "border-primary ring-2 ring-primary/40"
                        : "border-transparent hover:border-primary/40"
                    }`}
                    key={token}
                    onClick={() => setColor(token)}
                    type="button"
                  >
                    <span
                      aria-hidden="true"
                      className={`size-3 rounded-full ${dotFor(token)}`}
                    />
                    {token}
                  </button>
                );
              })}
            </div>
          </fieldset>
          {error ? <p className="text-xs text-danger">{error}</p> : null}
        </div>
        <footer className="flex items-center justify-between gap-2 border-t border-border bg-surface-muted/50 px-5 py-4">
          {mode === "edit" && tag ? (
            <Button
              onClick={() => setConfirmDelete(true)}
              type="button"
              variant="ghost"
            >
              <Trash2 aria-hidden="true" className="size-4 text-danger" />
              删除
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button onClick={onClose} type="button" variant="ghost">
              取消
            </Button>
            <Button
              disabled={duplicateName}
              loading={pending}
              type="submit"
            >
              {mode === "create" ? "创建" : "保存"}
            </Button>
          </div>
        </footer>
      </form>
      <ConfirmDialog
        cancelLabel="取消"
        confirmLabel="删除"
        description={
          tag
            ? `即将永久删除标签“${tag.name}”。当前关联 ${tag.document_count ?? 0} 份文档，删除后文档将不再被分类到该标签。`
            : "即将删除标签。"
        }
        destructive
        onConfirm={async () => {
          if (!tag) return;
          await apiClient.deleteDocumentTag(collectionId, tag.id);
          onSaved();
          onClose();
        }}
        onOpenChange={(next) => {
          if (!next) setConfirmDelete(false);
        }}
        open={confirmDelete}
        title={`删除标签“${tag?.name ?? ""}”？`}
      />
    </Dialog>
  );
}

function messageFor(reason: unknown, fallback: string) {
  if (reason instanceof ApiError) return `${reason.code}：${reason.message}`;
  if (reason instanceof Error) return reason.message;
  return fallback;
}

function dotFor(token: TagColor) {
  switch (token) {
    case "grey":
      return "bg-slate-400";
    case "blue":
      return "bg-blue-500";
    case "green":
      return "bg-emerald-500";
    case "amber":
      return "bg-amber-500";
    case "red":
      return "bg-rose-500";
    case "purple":
      return "bg-violet-500";
  }
}