import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { DocumentsView } from "./documents-view";
import { collection, document, ids } from "@/test/mocks/fixtures";
import { mockServer } from "@/test/mocks/server";

describe("DocumentsView pagination", () => {
  it("loads 20 documents at a time and follows the server cursor", async () => {
    const requests = vi.fn();
    const secondDocument = { ...document, id: "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e70", filename: "page-two.md" };
    mockServer.use(
      http.get("/api/v1/collections/:id", () => HttpResponse.json({ ...collection, document_count: 21 })),
      http.get("/api/v1/collections/:id/documents", ({ request }) => {
        const url = new URL(request.url);
        requests(url.searchParams.get("limit"), url.searchParams.get("cursor"));
        const secondPage = url.searchParams.get("cursor") === "page-2";
        return HttpResponse.json({
          items: [secondPage ? secondDocument : document],
          page_info: { next_cursor: secondPage ? null : "page-2", has_more: !secondPage },
        });
      }),
    );

    const user = userEvent.setup();
    render(<DocumentsView collectionId={ids.collection} embedded />);

    expect(await screen.findByText(document.filename)).toBeInTheDocument();
    expect(screen.getByText("共 21 条 · 第 1 / 2 页 · 每页最多 20 条")).toBeInTheDocument();
    expect(requests).toHaveBeenLastCalledWith("20", null);

    await user.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText(secondDocument.filename)).toBeInTheDocument();
    expect(screen.getByText("共 21 条 · 第 2 / 2 页 · 每页最多 20 条")).toBeInTheDocument();
    expect(requests).toHaveBeenLastCalledWith("20", "page-2");
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  });

  it("keeps the current knowledge base fixed in the embedded upload dialog", async () => {
    const user = userEvent.setup();
    render(<DocumentsView collectionId={ids.collection} embedded />);

    await screen.findByText(document.filename);
    await user.click(screen.getByRole("button", { name: "上传文件" }));

    expect(screen.getByText("文档将上传至「测试知识库」，并自动完成解析与索引。")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "知识库" })).not.toBeInTheDocument();
    expect(screen.getByText(/PDF、DOCX、XLSX、CSV、PPTX/)).toBeInTheDocument();
  });
});
