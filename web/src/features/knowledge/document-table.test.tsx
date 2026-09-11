import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { document, tag } from "@/test/mocks/fixtures";
import { DocumentTable, type DocumentRow } from "./document-table";
import { tagPillClass } from "./tag-color";

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
    const checkbox = await screen.findByRole("checkbox");
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: "保存标签" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: "编辑文档标签" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("maps each tag's colour to its pill class", () => {
    render(
      <DocumentTable
        documents={[
          {
            ...document,
            status: "ready" as const,
            tags: [tag, { ...tag, id: "tag-2", color: "red", name: "复盘" }],
          },
        ]}
      />,
    );
    const blue = screen.getByText("重要");
    const red = screen.getByText("复盘");
    expect(blue.className).toContain(tagPillClass("blue").split(" ").pop());
    expect(red.className).toContain(tagPillClass("red").split(" ").pop());
  });

  it("collapses overflow tags behind a +N indicator", () => {
    render(
      <DocumentTable
        documents={[
          {
            ...document,
            status: "ready" as const,
            tags: [
              tag,
              { ...tag, id: "tag-2", color: "green", name: "重要2" },
              { ...tag, id: "tag-3", color: "purple", name: "重要3" },
              { ...tag, id: "tag-4", color: "amber", name: "重要4" },
            ],
          },
        ]}
      />,
    );
    expect(screen.getByText("重要")).toBeInTheDocument();
    expect(screen.queryByText("重要4")).not.toBeInTheDocument();
    expect(screen.getByText("+2")).toBeInTheDocument();
    expect(screen.getByText("+2").title).toContain("重要3");
    expect(screen.getByText("+2").title).toContain("重要4");
  });

  it("renders a half-checked header when only some rows are selected", async () => {
    const second: DocumentRow = {
      ...(document as DocumentRow),
      id: "second-doc",
      filename: "second.pdf",
    };
    const onSelectionChange = (next: Set<string>) => {
      selected = next;
    };
    let selected = new Set<string>([document.id]);
    render(
      <DocumentTable
        documents={[document as DocumentRow, second]}
        selectedIds={selected}
        onSelectionChange={onSelectionChange}
      />,
    );
    const header = screen.getByRole("checkbox", { name: "全选当前页" });
    expect(header).not.toBeChecked();
    expect((header as HTMLInputElement).indeterminate).toBe(true);
    fireEvent.click(header);
    expect(selected.size).toBe(2);
  });
});
