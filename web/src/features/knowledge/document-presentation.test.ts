import { describe, expect, it } from "vitest";
import { documentExtension, isSupportedDocument, parsingMethod } from "./document-presentation";

describe("document presentation", () => {
  it.each(["a.pdf", "a.docx", "a.xlsx", "a.csv", "a.pptx", "a.txt", "a.md", "a.html", "a.mhtml", "a.epub", "a.xmind", "a.png", "a.jpeg", "a.webp"])(
    "accepts the supported format %s",
    (filename) => expect(isSupportedDocument(filename)).toBe(true),
  );

  it("does not advertise gated legacy office formats", () => {
    expect(isSupportedDocument("legacy.doc")).toBe(false);
    expect(isSupportedDocument("legacy.xls")).toBe(false);
    expect(isSupportedDocument("legacy.ppt")).toBe(false);
  });

  it("derives user-facing format and parsing labels", () => {
    expect(documentExtension("guide.markdown")).toBe("MD");
    expect(parsingMethod("table.xlsx")).toBe("表格解析");
    expect(parsingMethod("scan.jpg")).toBe("视觉解析");
    expect(parsingMethod("guide.pdf")).toBe("标准解析");
  });
});
