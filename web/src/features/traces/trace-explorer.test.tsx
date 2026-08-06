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
    expect(screen.getByText("dense_retrieval")).toBeInTheDocument();
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
});
