import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { DocumentsView } from "./documents-view";
import {
  collection,
  document,
  folderChild,
  folderRoot,
  ids,
  tag,
} from "@/test/mocks/fixtures";
import { mockServer } from "@/test/mocks/server";

function renderEmbedded() {
  return render(
    <DocumentsView collectionId={ids.collection} embedded />,
  );
}

describe("DocumentsView pagination", () => {
  it("loads 20 documents at a time and follows the server cursor", async () => {
    const requests = vi.fn();
    const secondDocument = {
      ...document,
      id: "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e70",
      filename: "page-two.md",
    };
    mockServer.use(
      http.get("/api/v1/collections/:id", () =>
        HttpResponse.json({ ...collection, document_count: 21 }),
      ),
      http.get("/api/v1/collections/:id/documents", ({ request }) => {
        const url = new URL(request.url);
        requests(url.searchParams.get("limit"), url.searchParams.get("cursor"));
        const secondPage = url.searchParams.get("cursor") === "page-2";
        return HttpResponse.json({
          items: [secondPage ? secondDocument : document],
          page_info: {
            next_cursor: secondPage ? null : "page-2",
            has_more: !secondPage,
          },
        });
      }),
    );

    const user = userEvent.setup();
    renderEmbedded();

    expect(await screen.findByText(document.filename)).toBeInTheDocument();
    expect(
      screen.getByText("共 21 条 · 第 1 / 2 页 · 每页最多 20 条"),
    ).toBeInTheDocument();
    expect(requests).toHaveBeenLastCalledWith("20", null);

    await user.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText(secondDocument.filename)).toBeInTheDocument();
    expect(
      screen.getByText("共 21 条 · 第 2 / 2 页 · 每页最多 20 条"),
    ).toBeInTheDocument();
    expect(requests).toHaveBeenLastCalledWith("20", "page-2");
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  });

  it("keeps the current knowledge base fixed in the embedded upload dialog", async () => {
    const user = userEvent.setup();
    renderEmbedded();

    await screen.findByText(document.filename);
    await user.click(screen.getByRole("button", { name: "上传文件" }));

    expect(
      screen.getByText(
        "文档将上传至「测试知识库」，并自动完成解析与索引。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "知识库" })).not.toBeInTheDocument();
    expect(screen.getByText(/PDF、DOCX、XLSX、CSV、PPTX/)).toBeInTheDocument();
  });
});

describe("DocumentsView filter bar", () => {
  it("renders all filter controls on the single-line toolbar", async () => {
    mockServer.use(
      http.get("/api/v1/collections/:id/folders", () =>
        HttpResponse.json({ items: [folderRoot, folderChild] }),
      ),
      http.get("/api/v1/collections/:id/tags", () =>
        HttpResponse.json({ items: [tag] }),
      ),
    );
    renderEmbedded();
    await screen.findByText(document.filename);
    expect(screen.getByPlaceholderText("搜索当前页文件名…")).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "按文件夹筛选" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "按状态筛选" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "文档排序" }),
    ).toBeInTheDocument();
    // "更多筛选" is collapsed by default on lg+
    expect(screen.getByText("更多筛选")).toBeInTheDocument();
  });

  it("syncs query and folder_id to the URL on change", async () => {
    const originalPathname = window.location.pathname;
    Object.defineProperty(window, "location", {
      value: { ...window.location, pathname: "/collections" },
      writable: true,
      configurable: true,
    });
    const replace = vi.fn();
    const original = window.history.replaceState;
    window.history.replaceState =
      replace as typeof window.history.replaceState;
    try {
      mockServer.use(
        http.get("/api/v1/collections/:id/folders", () =>
          HttpResponse.json({ items: [folderRoot] }),
        ),
      );
      const user = userEvent.setup();
      renderEmbedded();
      await screen.findByText(document.filename);
      await user.type(
        screen.getByPlaceholderText("搜索当前页文件名…"),
        "向量",
      );
      await user.selectOptions(
        screen.getByRole("combobox", { name: "按文件夹筛选" }),
        folderRoot.id,
      );
      const last = String(replace.mock.calls.at(-1)?.[2]);
      expect(last.startsWith("/collections")).toBe(true);
      const url = new URL(last, "http://localhost");
      expect(url.searchParams.get("q")).toBe("向量");
      expect(url.searchParams.get("folder_id")).toBe(folderRoot.id);
    } finally {
      window.history.replaceState = original;
      Object.defineProperty(window, "location", {
        value: { ...window.location, pathname: originalPathname },
        writable: true,
        configurable: true,
      });
    }
  });

  it("shows a chip per active filter and clears each individually", async () => {
    const user = userEvent.setup();
    renderEmbedded();
    await screen.findByText(document.filename);
    await user.type(
      screen.getByPlaceholderText("搜索当前页文件名…"),
      "向量",
    );
    const chip = await screen.findByRole("button", { name: /清除关键词：向量/ });
    await user.click(chip);
    expect(
      screen.queryByRole("button", { name: /清除关键词/ }),
    ).not.toBeInTheDocument();
  });
});

describe("DocumentsView batch toolbar", () => {
  it("only renders the batch toolbar when at least one row is selected", async () => {
    const user = userEvent.setup();
    renderEmbedded();
    await screen.findByText(document.filename);
    expect(
      screen.queryByText(/已选 \d+ 项/),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("checkbox", { name: `选择 ${document.filename}` }),
    );
    expect(screen.getByText(/已选 1 项/)).toBeInTheDocument();
  });

  it("opens a confirm dialog before batch-deleting", async () => {
    const user = userEvent.setup();
    renderEmbedded();
    await screen.findByText(document.filename);
    await user.click(
      screen.getByRole("checkbox", { name: `选择 ${document.filename}` }),
    );
    await user.click(screen.getByRole("button", { name: "批量删除" }));
    expect(
      await screen.findByRole("heading", { name: /确认删除 1 份文档/ }),
    ).toBeInTheDocument();
  });

  it("disables batch reprocess when the selection exceeds the limit", async () => {
    mockServer.use(
      http.get("/api/v1/collections/:id/documents", () =>
        HttpResponse.json({
          items: Array.from({ length: 21 }, (_, i) => ({
            ...document,
            id: `doc-${i}`,
            filename: `doc-${i}.pdf`,
          })),
          page_info: { next_cursor: null, has_more: false },
        }),
      ),
    );
    const user = userEvent.setup();
    renderEmbedded();
    await screen.findByText("doc-0.pdf");
    await user.click(
      screen.getByRole("checkbox", { name: "全选当前页" }),
    );
    const reprocess = await screen.findByRole("button", {
      name: "重新解析",
    });
    expect(reprocess).toBeDisabled();
    expect(reprocess.title).toContain("20");
  });
});
