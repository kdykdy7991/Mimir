import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  BATCH_LIMIT_GENERAL,
  BATCH_LIMIT_REPROCESS,
  BatchResultPanel,
  BatchToolbar,
} from "./batch-toolbar";
import { folderRoot, ids, tag } from "@/test/mocks/fixtures";

describe("BatchToolbar", () => {
  it("shows the selected count and disables the reprocess button above the limit", () => {
    render(
      <BatchToolbar
        folders={[folderRoot]}
        pending={false}
        selectedCount={BATCH_LIMIT_REPROCESS + 1}
        tags={[tag]}
        tagAction="add"
        onApplyTag={() => undefined}
        onClearSelection={() => undefined}
        onDelete={() => undefined}
        onMove={() => undefined}
        onReprocess={() => undefined}
        onTagActionChange={() => undefined}
      />,
    );
    expect(
      screen.getByText(`已选 ${BATCH_LIMIT_REPROCESS + 1} 项`),
    ).toBeInTheDocument();
    const reprocess = screen.getByRole("button", { name: "重新解析" });
    expect(reprocess).toBeDisabled();
    expect(reprocess.title).toContain(String(BATCH_LIMIT_REPROCESS));
  });

  it("surfaces the general-limit warning above the cap of 100", () => {
    render(
      <BatchToolbar
        folders={[]}
        pending={false}
        selectedCount={BATCH_LIMIT_GENERAL + 1}
        tags={[]}
        tagAction="add"
        onApplyTag={() => undefined}
        onClearSelection={() => undefined}
        onDelete={() => undefined}
        onMove={() => undefined}
        onReprocess={() => undefined}
        onTagActionChange={() => undefined}
      />,
    );
    expect(screen.getByText(/超过 100 项上限/)).toBeInTheDocument();
  });

  it("opens a confirm dialog before triggering batch delete", async () => {
    const onDelete = vi.fn();
    const user = userEvent.setup();
    render(
      <BatchToolbar
        folders={[]}
        pending={false}
        selectedCount={3}
        tags={[]}
        tagAction="add"
        onApplyTag={() => undefined}
        onClearSelection={() => undefined}
        onDelete={onDelete}
        onMove={() => undefined}
        onReprocess={() => undefined}
        onTagActionChange={() => undefined}
      />,
    );
    await user.click(screen.getByRole("button", { name: "批量删除" }));
    const dialog = await screen.findByRole("dialog", {
      name: /确认删除 3 份文档/,
    });
    await user.click(
      within(dialog).getByRole("button", { name: "删除文档" }),
    );
    await waitFor(() => expect(onDelete).toHaveBeenCalled());
  });

  it("emits a tag action when a tag is selected", async () => {
    const onApplyTag = vi.fn();
    const onTagActionChange = vi.fn();
    const user = userEvent.setup();
    render(
      <BatchToolbar
        folders={[]}
        pending={false}
        selectedCount={2}
        tags={[tag]}
        tagAction="add"
        onApplyTag={onApplyTag}
        onClearSelection={() => undefined}
        onDelete={() => undefined}
        onMove={() => undefined}
        onReprocess={() => undefined}
        onTagActionChange={onTagActionChange}
      />,
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "批量标签操作" }),
      "replace",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "选择批量标签" }),
      tag.id,
    );
    expect(onTagActionChange).toHaveBeenCalledWith("replace");
    expect(onApplyTag).toHaveBeenCalledWith(tag.id);
  });
});

describe("BatchResultPanel", () => {
  it("summarises success and failure counts", () => {
    render(
      <BatchResultPanel
        result={{
          items: [
            { document_id: ids.document, status: "success", task_id: null, error: null },
            {
              document_id: "doc-2",
              status: "error",
              task_id: "task-2",
              error: { code: "UPSERT_FAILED", message: "写入失败" },
            },
          ],
        }}
      />,
    );
    expect(screen.getByText(/共 2 项，成功 1 项，失败 1 项/)).toBeInTheDocument();
    expect(screen.getByText(/部分失败/)).toBeInTheDocument();
  });
});
