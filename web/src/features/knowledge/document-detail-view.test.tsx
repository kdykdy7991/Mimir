import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ids } from "@/test/mocks/fixtures";
import { DocumentDetailView } from "./document-detail-view";

describe("DocumentDetailView", () => {
  it("renders original-file preview, table count, and warnings", async () => {
    render(<DocumentDetailView id={ids.document} />);

    expect(await screen.findByRole("heading", { name: "rag-guide.pdf" })).toBeInTheDocument();
    expect(screen.getAllByText("测试知识库")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "原始文件预览" })).toBeInTheDocument();
    expect(screen.getByTitle("rag-guide.pdf 原文件预览")).toHaveAttribute(
      "src",
      expect.stringContaining(`/api/v1/documents/${ids.document}/preview`),
    );
    expect(screen.getByText("表格识别")).toBeInTheDocument();
    expect(screen.getByText("PDF · 3 页")).toBeInTheDocument();
    expect((await screen.findAllByText("RAG 指南")).length).toBeGreaterThan(0);
    expect(screen.getByText("第 3 页存在低置信度文本")).toBeInTheDocument();
    expect(screen.queryByText("后端暂未提供")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /查看完整 Trace/ })).toHaveAttribute(
      "href",
      `/traces?type=ingestion&id=${ids.task}`,
    );
  });
});
