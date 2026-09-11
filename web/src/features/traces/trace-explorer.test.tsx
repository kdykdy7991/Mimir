import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ids } from "@/test/mocks/fixtures";
import { TraceExplorer } from "./trace-explorer";

describe("TraceExplorer", () => {
  it("loads history and opens a selected trace without exposing raw details", async () => {
    render(<TraceExplorer />);

    expect(await screen.findByText(ids.query)).toBeInTheDocument();
    expect(screen.getByText(ids.task)).toBeInTheDocument();

    fireEvent.click(screen.getByText(ids.query));
    expect(await screen.findByText("语义检索")).toBeInTheDocument();
    expect(screen.getByDisplayValue(ids.query)).toBeInTheDocument();
    expect(screen.queryByText("records")).not.toBeInTheDocument();
  });
});
