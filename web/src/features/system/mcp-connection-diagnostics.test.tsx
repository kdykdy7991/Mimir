import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MCPConnectionDiagnostics } from "./mcp-connection-diagnostics";

describe("MCPConnectionDiagnostics", () => {
  it("renders successful protocol stages and safe statistics", () => {
    render(<MCPConnectionDiagnostics result={{ ok: true, error: null, tested_at: "2026-09-11T03:00:00Z", stages: [
      { name: "connect", status: "success", latency_ms: 8 },
      { name: "initialize", status: "success", latency_ms: 12 },
      { name: "tools_list", status: "success", tool_count: 5 },
      { name: "list_collections", status: "success", collection_count: 2 },
    ] }} />);
    expect(screen.getByText("全部通过")).toBeInTheDocument();
    expect(screen.getByText("5 个工具")).toBeInTheDocument();
    expect(screen.getByText("2 个知识库")).toBeInTheDocument();
  });

  it("shows backend-classified guidance without needing a secret", () => {
    render(<MCPConnectionDiagnostics result={{ ok: false, tested_at: "2026-09-11T03:00:00Z", stages: [
      { name: "connect", status: "success" }, { name: "initialize", status: "failed" }, { name: "tools_list", status: "skipped" },
    ], error: { code: "client_unauthorized", message: "客户端认证失败", suggested_action: "请确认使用 MCP Client Key。", request_id: "request-safe" } }} />);
    expect(screen.getByText("测试未通过")).toBeInTheDocument();
    expect(screen.getByText(/确认使用的是 MCP Client Key/)).toBeInTheDocument();
    expect(screen.getByText(/request-safe/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("api_key");
  });
});
