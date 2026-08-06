import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { QueryPlayground } from "./query-playground";
import { mockServer } from "@/test/mocks/server";
import { queryResponse } from "@/test/mocks/fixtures";

describe("QueryPlayground", () => {
  it("runs a real-shaped v0.2 query and renders citations", async () => {
    const user = userEvent.setup(); render(<QueryPlayground />);
    await screen.findByRole("option", { name: "测试知识库" });
    await user.type(screen.getByLabelText("向知识库检索"), "什么是混合检索？");
    await user.click(screen.getByRole("button", { name: "运行检索" }));
    expect(await screen.findByText("混合检索会结合语义召回与关键词召回。")).toBeInTheDocument();
    expect(screen.getByText("MCP Server 与 Agent 职责分离")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /查看完整 Trace/ })).toHaveAttribute("href", expect.stringContaining(queryResponse.query_id));
  });

  it("renders degraded reasons and an empty result", async () => {
    mockServer.use(http.post("/api/v1/collections/:id/queries", () => HttpResponse.json({ ...queryResponse, citations: [], diagnostics: { ...queryResponse.diagnostics, degraded: true, degraded_reasons: ["no chunks matched the query"] } })));
    const user = userEvent.setup(); render(<QueryPlayground />);
    await screen.findByRole("option", { name: "测试知识库" });
    await user.type(screen.getByLabelText("向知识库检索"), "没有答案的问题");
    await user.click(screen.getByRole("button", { name: "运行检索" }));
    expect(await screen.findByText("没有检索结果")).toBeInTheDocument();
    expect(screen.getByText(/no chunks matched/)).toBeInTheDocument();
  });

  it("maps an upstream failure to actionable UI", async () => {
    mockServer.use(http.post("/api/v1/collections/:id/queries", () => HttpResponse.json({ error: { code: "UPSTREAM_ERROR", message: "provider failed", request_id: "req-502", details: {} } }, { status: 502 })));
    render(<QueryPlayground />); await screen.findByRole("option", { name: "测试知识库" });
    fireEvent.change(screen.getByLabelText("向知识库检索"), { target: { value: "触发错误" } });
    fireEvent.click(screen.getByRole("button", { name: "运行检索" }));
    expect(await screen.findByText("向量服务暂时不可用。可切换 Sparse 模式重试，或检查 Provider 配置。")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/UPSTREAM_ERROR/)).toBeInTheDocument());
  });
});
