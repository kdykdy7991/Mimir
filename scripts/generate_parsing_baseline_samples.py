"""Generate the Phase-0 fixed parsing baseline sample corpus.

Usage::

    python scripts/generate_parsing_baseline_samples.py [--out DIR]

Writes a small, deterministic, non-sensitive corpus used to measure the
pre-migration parsing baseline and, later, the migrated DocReader output.

Why this script exists
----------------------
The plan (docs/plan-2026-09-09-weknora-local-document-parser-migration.md,
Phase 0) requires a *fixed* sample set so acceptance gates (Phase 13) are
measured against the same inputs, not "by eye". Regenerating samples from
this script guarantees any engineer can rebuild an identical corpus.

Coverage (parseable by the pre-migration legacy loader = PDF + Markdown)
-----------------------------------------------------------------------
- ``single_column.pdf``      single-column digital text PDF
- ``two_column.pdf``         two-column layout (exposes the legacy loader's
                             `(y_top, x_left)` sorting interleaving bug)
- ``bordered_table.pdf``     bordered table
- ``borderless_table.pdf``   borderless table (row/col inference is ambiguous)
- ``cross_page_table.pdf``   a table spanning two pages
- ``scanned.pdf``            image-only scanned page (no text layer)
- ``docs.md``                multi-section Markdown containing a GFM table

DOCX / XLSX / PPTX are intentionally NOT generated here: the pre-migration
baseline chain only supports PDF + Markdown, and generating those requires
openpyxl / python-docx / python-pptx which are not (yet) project deps. Their
*expected-structure descriptors* live in
``docs/baselines/document-parsing/descriptors/`` and real binaries are added
when each format is migrated in Phase 6 (avoiding a Phase-0-only dependency
injection).

Runtime deps: pymupdf (production PDF stack) + Pillow for the scanned page.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf  # type: ignore[import-untyped]
from PIL import Image

DEFAULT_OUT = Path("docs/baselines/document-parsing/samples")

# Simple deterministic pseudo-content. Non-sensitive, human-readable numbers
# that let a later evaluator assert on exact cell values. Note: PDF sample
# text is kept ASCII-only because the production PDF stack (pymupdf base-14
# fonts) cannot render CJK glyphs; CJK fidelity is covered by the Markdown
# sample and by later Phase-6 format fixtures once real-type fixtures exist.
TABLE_HEADER = ["ID", "Name", "Qty", "Price", "Total"]
TABLE_PAGE_1_ROWS = [
    ["A-1001", "Notebook", "10", "12.50", "125.00"],
    ["A-1002", "Gel-Pen", "20", "1.80", "36.00"],
    ["A-1003", "Folder", "5", "8.20", "41.00"],
    ["A-1004", "Stapler", "2", "15.00", "30.00"],
    ["A-1005", "Labels", "30", "0.60", "18.00"],
    ["A-1006", "Whiteout", "8", "3.50", "28.00"],
    ["A-1007", "Calculator", "1", "45.00", "45.00"],
    # cross-page continued rows:
    ["B-2001", "Toner", "3", "120.00", "360.00"],
    ["B-2002", "InkCart", "4", "60.00", "240.00"],
    ["B-2003", "Paper-Ream", "6", "25.00", "150.00"],
    ["B-2004", "USB-Drive", "2", "55.00", "110.00"],
]
TABLE_PAGE_2_ROWS = [
    ["B-2005", "Keyboard", "1", "99.00", "99.00"],
    ["B-2006", "Mouse", "2", "45.00", "90.00"],
    ["C-3001", "Monitor", "1", "899.00", "899.00"],
]


def _make_page(doc: pymupdf.Document, text: str) -> pymupdf.Page:
    """Create a fresh A4 page and write text at the top."""
    page = doc.new_page(width=595, height=842)  # A4 @72dpi
    y = 60
    for line in text.split("\n"):
        page.insert_text((60, y), line, fontsize=10)
        y += 16
        if y > 820:
            break
    return page


def _write_table(
    page: pymupdf.Page,
    header: list[str],
    rows: list[list[str]],
    *,
    bordered: bool,
    start_y: float = 90,
    x: float = 60,
    col_w: float = 78,
    row_h: float = 18,
) -> None:
    """Draw a table of ``header`` + ``rows`` onto ``page``.

    ``bordered=False`` draws only the text, so row/column structure must be
    inferred from spacing — this is the classic *borderless table* case.
    """
    # Monospace-ish spacing helps keep visual columns aligned.
    table = [header, *rows]
    for r, row in enumerate(table):
        y = start_y + r * row_h
        for c, cell in enumerate(row):
            cell_x = x + c * col_w
            page.insert_text((cell_x + 2, y + 12), cell, fontsize=9)
            if bordered:
                page.draw_rect(
                    pymupdf.Rect(cell_x, y, cell_x + col_w, y + row_h),
                    width=0.8,
                    color=(0.1, 0.1, 0.1),
                )


def _single_column(doc: pymupdf.Document) -> None:
    text = (
        "Chapter 1: Overview\n"
        "\n"
        "This is a single-column digital PDF sample used to verify basic text "
        "extraction. It contains a longer first sentence so that the text can be "
        "split and retrieved by the chunker.\n"
        "\n"
        "Section 2: Conclusion\n"
        "A single-column page does not need complex reading-order recovery.\n"
    )
    _make_page(doc, text)


def _two_column(doc: pymupdf.Document) -> None:
    """Two columns laid out side by side (left then right reading order).

    The legacy loader sorts lines by ``(y_top, x_left)``; with real two-column
    layouts different columns rarely share an exact y, so lines interleave
    column-by-column producing a garbled order. This sample is the regression
    that Phase 2's multi-column detection must fix.
    """
    page = doc.new_page(width=595, height=842)
    left = [
        "LEFT-C column header of the fixture",
        "LEFT-C paragraph one.",
        "LEFT-C paragraph two references value 10.",
        "LEFT-C paragraph three references value 30.",
    ]
    right = [
        "RIGHT-C paragraph one.",
        "RIGHT-C paragraph two references value 20.",
    ]
    y = 80
    for line in left:
        page.insert_text((60, y), line, fontsize=9)
        y += 20
    y = 80
    for line in right:
        page.insert_text((330, y), line, fontsize=9)
        y += 20
    # footer line that appears on both columns
    page.insert_text((60, 810), "FOOTER: page 1", fontsize=8)
    page.insert_text((330, 810), "FOOTER: page 1", fontsize=8)


def _bordered_table(doc: pymupdf.Document) -> None:
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 60), "Bordered table example", fontsize=11)
    _write_table(page, TABLE_HEADER, TABLE_PAGE_1_ROWS[:5], bordered=True)


def _borderless_table(doc: pymupdf.Document) -> None:
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 60), "Borderless table example", fontsize=11)
    _write_table(page, TABLE_HEADER, TABLE_PAGE_1_ROWS[:5], bordered=False)


def _cross_page_table(doc: pymupdf.Document) -> None:
    """A header row on page 1, continued rows flowing onto page 2."""
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((60, 60), "Cross-page table example (page 1)", fontsize=11)
    _write_table(
        p1, TABLE_HEADER, TABLE_PAGE_1_ROWS, bordered=True,
        start_y=90, row_h=20,
    )
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((60, 60), "Cross-page table example (page 2, continued)", fontsize=11)
    _write_table(
        p2, TABLE_HEADER, TABLE_PAGE_2_ROWS, bordered=True,
        start_y=70, row_h=20,
        # keep the header visible so the table is reproducible at a glance
    )


def _scanned(doc: pymupdf.Document, tmp_root: Path) -> None:
    """Image-only "scanned" page: render an image and embed it; no text layer."""
    # Build a small greyscale "scan" image with Pillow. The image is kept
    # small and mostly flat so the resulting PDF fixture stays tiny.
    img = Image.new("RGB", (300, 424), "white")
    px = img.load()
    for j in range(0, 424, 24):
        for i in range(0, 300):
            px[i, j] = (200, 200, 200)
    scan_path = tmp_root / "scan_placeholder.png"
    scan_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(scan_path, optimize=True)

    # Re-encode as a JPEG byte stream so the embedded page stays small.
    import io
    jpeg_buf = io.BytesIO()
    img.save(jpeg_buf, format="JPEG", quality=70)
    page = doc.new_page(width=595, height=842)
    page.insert_image(
        pymupdf.Rect(30, 40, 565, 802),
        stream=jpeg_buf.getvalue(),
    )


def _markdown(out: Path) -> None:
    out.write_text(
        "# 示例文档(Markdown)\n"
        "\n"
        "这是一个用于基准测试的 Markdown 样本,包含标题层级、段落和一张 GFM 表格。\n"
        "\n"
        "## 表格\n"
        "\n"
        "| 编号 | 名称 | 数量 | 单价 | 小计 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| A-1001 | 笔记本 | 10 | 12.50 | 125.00 |\n"
        "| A-1002 | 中性笔 | 20 | 1.80 | 36.00 |\n"
        "\n"
        "## 结尾\n"
        "文档结束。\n",
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    out = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)

    import tempfile

    # Emit each sample as its own single-document PDF. Intermediate mask
    # images for the scanned sample are written to a temp dir, never to
    # the committed samples directory.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        variants: dict[str, callable] = {
            "single_column": _single_column,
            "two_column": _two_column,
            "bordered_table": _bordered_table,
            "borderless_table": _borderless_table,
            "cross_page_table": _cross_page_table,
            "scanned": lambda d: _scanned(d, tmp_root),
        }
        for name, make in variants.items():
            source = pymupdf.open()
            make(source)
            source.save(out / f"{name}.pdf")
            source.close()

    _markdown(out / "docs.md")

    print(f"Wrote baseline samples to {out}")
    for p in sorted(out.glob("*")):
        print(f"  - {p.name} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()