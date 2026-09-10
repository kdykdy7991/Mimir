import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { TraceExplorer } from "./trace-explorer";
import { ids } from "@/test/mocks/fixtures";
import { mockServer } from "@/test/mocks/server";

describe("TraceExplorer", () => {
  it("loads an initial query trace and renders stages", async () => {
    render(<TraceExplorer initialId={ids.query} initialType="query" />);
    expect(await screen.findByText("查询链路")).toBeInTheDocument();
    expect(screen.getByText("语义检索")).toBeInTheDocument();
    expect(screen.getByText("fixture-embedding")).toBeInTheDocument();
  });

  it("shows the stable not-found error", async () => {
    mockServer.use(http.get("/api/v1/queries/:id/trace", () => HttpResponse.json({ error: { code: "QUERY_NOT_FOUND", message: "missing", request_id: "req-404", details: {} } }, { status: 404 })));
    const user = userEvent.setup(); render(<TraceExplorer />);
    await user.type(screen.getByLabelText("Trace ID"), ids.query);
    await user.click(screen.getByRole("button", { name: "读取 Trace" }));
    expect(await screen.findByText("没有找到对应 Trace，请确认类型与 ID 是否匹配。")).toBeInTheDocument();
    expect(screen.getByText(/QUERY_NOT_FOUND/)).toBeInTheDocument();
  });

  it("explains a skipped duplicate upload in Chinese", async () => {
    mockServer.use(http.get("/api/v1/ingestions/:id/trace", () => HttpResponse.json({
      id: ids.task,
      trace_type: "ingestion",
      started_at: "2026-09-10T00:00:00.000Z",
      finished_at: "2026-09-10T00:00:00.003Z",
      total_latency_ms: 3,
      stages: [{
        name: "ingestion_pipeline",
        method: null,
        provider: null,
        started_at: "2026-09-10T00:00:00.001Z",
        duration_ms: 0,
        details: { event: "skipped", file_hash: "2fade1659cef" },
      }],
      error: null,
    })));

    render(<TraceExplorer initialId={ids.task} initialType="ingestion" />);

    expect(await screen.findByText("重复文件检查")).toBeInTheDocument();
    expect(screen.getByText("文件内容未变化，沿用已有解析结果")).toBeInTheDocument();
    expect(screen.getByText("已跳过")).toBeInTheDocument();
  });
});
