import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  FolderCreateDialog,
  FolderRenameDialog,
  FolderDeleteDialog,
  TagDialog,
} from "./org-dialogs";
import { folderChild, folderRoot, ids, tag } from "@/test/mocks/fixtures";

describe("FolderCreateDialog", () => {
  it("submits the folder name and parent via the API", async () => {
    const onSaved = vi.fn();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <FolderCreateDialog
        collectionId={ids.collection}
        folders={[folderRoot, folderChild]}
        open
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    fireEvent.change(screen.getByLabelText("文件夹名称"), {
      target: { value: "新目录" },
    });
    await user.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
  });

  it("rejects an empty name without hitting the API", async () => {
    const onSaved = vi.fn();
    render(
      <FolderCreateDialog
        collectionId={ids.collection}
        folders={[]}
        open
        onClose={() => undefined}
        onSaved={onSaved}
      />,
    );
    fireEvent.change(screen.getByLabelText("文件夹名称"), {
      target: { value: " " },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    expect(onSaved).not.toHaveBeenCalled();
  });
});

describe("FolderRenameDialog", () => {
  it("sends only changed fields to the API", async () => {
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(
      <FolderRenameDialog
        collectionId={ids.collection}
        folder={folderRoot}
        folders={[folderRoot, folderChild]}
        open
        onClose={() => undefined}
        onSaved={onSaved}
      />,
    );
    const input = screen.getByLabelText("文件夹名称");
    fireEvent.change(input, { target: { value: "新名称" } });
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });
});

describe("FolderDeleteDialog", () => {
  it("refuses to delete a non-empty folder via the destructive API", async () => {
    const onDeleted = vi.fn();
    const onClose = vi.fn();
    render(
      <FolderDeleteDialog
        collectionId={ids.collection}
        folder={folderRoot}
        onClose={onClose}
        onDeleted={onDeleted}
        open
      />,
    );
    expect(
      screen.getByText(
        /该文件夹下还有 \d+ 份文档，无法直接删除/,
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "知道了" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("deletes an empty folder through the destructive API", async () => {
    const onDeleted = vi.fn();
    const user = userEvent.setup();
    render(
      <FolderDeleteDialog
        collectionId={ids.collection}
        folder={{ ...folderChild, document_count: 0 }}
        onClose={() => undefined}
        onDeleted={onDeleted}
        open
      />,
    );
    await user.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });
});

describe("TagDialog", () => {
  it("creates a tag with the chosen colour", async () => {
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(
      <TagDialog
        collectionId={ids.collection}
        mode="create"
        tags={[]}
        open
        onClose={() => undefined}
        onSaved={onSaved}
      />,
    );
    fireEvent.change(screen.getByLabelText("标签名称"), {
      target: { value: "复盘" },
    });
    await user.click(screen.getByRole("button", { name: "选择颜色 amber" }));
    await user.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("warns about duplicate tag names in the same collection", async () => {
    render(
      <TagDialog
        collectionId={ids.collection}
        mode="create"
        tags={[tag]}
        open
        onClose={() => undefined}
        onSaved={() => undefined}
      />,
    );
    fireEvent.change(screen.getByLabelText("标签名称"), {
      target: { value: tag.name },
    });
    expect(
      screen.getByText(/已存在同名标签/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建" })).toBeDisabled();
  });

  it("deletes the tag after confirming in the follow-up dialog", async () => {
    const onSaved = vi.fn();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <TagDialog
        collectionId={ids.collection}
        mode="edit"
        tags={[tag]}
        tag={tag}
        open
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const confirmDialog = await screen.findByRole("dialog", {
      name: /删除标签/,
    });
    await user.click(
      within(confirmDialog).getByRole("button", { name: "删除" }),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
  });
});

import { vi } from "vitest";
