import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./status-badge";

describe("StatusBadge", () => {
  it("renders known status with its mapped label", () => {
    render(<StatusBadge status="ready" />);
    expect(screen.getByText("已就绪")).toBeInTheDocument();
  });

  it("overrides label when provided", () => {
    render(<StatusBadge label="自定义" status="ready" />);
    expect(screen.getByText("自定义")).toBeInTheDocument();
    expect(screen.queryByText("已就绪")).not.toBeInTheDocument();
  });

  it("falls back to raw status text for unknown statuses", () => {
    render(<StatusBadge status="weird-state" />);
    expect(screen.getByText("weird-state")).toBeInTheDocument();
  });

  it("maps degraded / failed / succeeded", () => {
    render(<StatusBadge status="degraded" />);
    expect(screen.getByText("降级")).toBeInTheDocument();
    render(<StatusBadge status="failed" />);
    expect(screen.getByText("失败")).toBeInTheDocument();
    render(<StatusBadge status="succeeded" />);
    expect(screen.getByText("已完成")).toBeInTheDocument();
  });
});
