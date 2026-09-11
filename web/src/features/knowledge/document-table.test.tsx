import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { document } from "@/test/mocks/fixtures";
import { DocumentTable } from "./document-table";

describe("DocumentTable", () => {
  it("constrains long filenames and exposes the complete name as a tooltip", () => {
    const filename = `${"非常长的文档名称".repeat(16)}.pdf`;
    render(
      <DocumentTable
        documents={[{ ...document, filename, status: "ready" as const }]}
      />,
    );

    const link = screen.getByRole("link", { name: filename });
    expect(link).toHaveClass("block", "max-w-full", "truncate");
    expect(link).toHaveAttribute("title", filename);
  });

  it("searches and full-replaces one document's tags", async () => {
    render(
      <DocumentTable
        documents={[{ ...document, status: "ready" as const }]}
        onDelete={() => undefined}
        onChanged={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /编辑.*的标签/ }));
    expect(
      await screen.findByRole("heading", { name: "编辑文档标签" }),
    ).toBeInTheDocument();
    const tag = await screen.findByRole("checkbox");
    fireEvent.click(tag);
    fireEvent.click(screen.getByRole("button", { name: "保存标签" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: "编辑文档标签" }),
      ).not.toBeInTheDocument(),
    );
  });
});
