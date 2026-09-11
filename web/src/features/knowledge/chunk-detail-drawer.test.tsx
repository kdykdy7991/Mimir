import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentChunkDetail } from "@/types";
import { ChunkDetailDrawer } from "./chunk-detail-drawer";

const chunk: DocumentChunkDetail = {
  chunk_id: "chunk-2", document_id: "doc-1", index: 1, text: "完整的制度正文", heading: "休假制度", page: 8,
  content_type: "text", character_count: 8, previous_chunk_id: "chunk-1", next_chunk_id: "chunk-3",
  source_locator: { kind: "pdf_page", page: 8 },
};

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) { this.setAttribute("open", ""); });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) { this.removeAttribute("open"); this.dispatchEvent(new Event("close")); });
});

describe("ChunkDetailDrawer", () => {
  it("shows full content and navigates adjacent chunks", async () => {
    const onNavigate = vi.fn();
    const onShowSource = vi.fn();
    render(<ChunkDetailDrawer chunk={chunk} onClose={vi.fn()} onNavigate={onNavigate} onRetry={vi.fn()} onShowSource={onShowSource} open />);

    expect(screen.getByText("完整的制度正文")).toBeInTheDocument();
    expect(screen.getByText("休假制度")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "下一段" }));
    expect(onNavigate).toHaveBeenCalledWith("chunk-3");
    await userEvent.click(screen.getByRole("button", { name: "在原文中查看" }));
    expect(onShowSource).toHaveBeenCalledWith(chunk);
  });

  it("disables navigation at document boundaries", () => {
    render(<ChunkDetailDrawer chunk={{ ...chunk, previous_chunk_id: null, next_chunk_id: null }} onClose={vi.fn()} onNavigate={vi.fn()} onRetry={vi.fn()} onShowSource={vi.fn()} open />);
    expect(screen.getByRole("button", { name: "上一段" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下一段" })).toBeDisabled();
  });

  it("copies text without exposing it outside the user action", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<ChunkDetailDrawer chunk={chunk} onClose={vi.fn()} onNavigate={vi.fn()} onRetry={vi.fn()} onShowSource={vi.fn()} open />);

    await userEvent.click(screen.getByRole("button", { name: "复制正文" }));
    expect(writeText).toHaveBeenCalledWith(chunk.text);
    expect(await screen.findByText("Chunk 正文已复制。")).toBeInTheDocument();
  });

  it("renders retryable errors and restores focus when closed", async () => {
    const onClose = vi.fn();
    const onRetry = vi.fn();
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();
    render(<ChunkDetailDrawer error={new Error("upstream unavailable")} onClose={onClose} onNavigate={vi.fn()} onRetry={onRetry} onShowSource={vi.fn()} open />);

    await userEvent.click(screen.getByRole("button", { name: /重试/ }));
    expect(onRetry).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "关闭 Chunk 详情" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });
});
