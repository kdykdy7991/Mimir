"""Regression tests: numeric table cells must survive the PDF layout path.

Regression: the DocReader builtin PDF parser dropped numeric table cells
(e.g. ``12.50``/``125.00``) from bordered / borderless / cross-page table
samples. The glyph-level layout reconstruction (``pdf_layout.py``) keeps the
cells; the loss happened one stage later in the text pipeline when
``strip_chart_text_debris`` (``pdf_postprocess.py``) mislabelled a run of
consecutive all-numeric lines (a table column of prices) as chart axis-tick
debris and flushed it. A solitary decimal value like ``12.50`` is a legitimate
table cell, so it must not be treated as debris.

These tests prove numeric cells survive the FULL parser on the committed
baseline fixtures and on pymupdf-generated bordered/borderless tables.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docreader.parser.pdf_postprocess import (
    postprocess_pdf_text,
    strip_chart_text_debris,
)
from docreader.parser.parser import Parser

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SAMPLES = _REPO_ROOT / "docs" / "baselines" / "document-parsing" / "samples"

_REAL_EXPECTATIONS = {
    "bordered_table.pdf": ("12.50", "125.00", "18.00", "41.00"),
    "borderless_table.pdf": ("12.50", "41.00", "125.00", "18.00"),
    "cross_page_table.pdf": ("12.50", "125.00", "360.00", "90.00", "41.00", "18.00"),
}


# ---------------------------------------------------------------------------
# Unit: the chart-debris stripper must preserve table cells.
# ---------------------------------------------------------------------------
def test_chart_debris_preserves_table_numeric_column() -> None:
    # A table column of prices is a run of consecutive decimal lines; it is
    # real content and must survive (regression trigger).
    text = (
        "Price\n"
        "12.50\n1.80\n8.20\n15.00\n0.60\n"
        "Total\n"
        "125.00\n36.00\n41.00\n30.00\n18.00"
    )
    out = strip_chart_text_debris(text)
    assert "12.50" in out
    assert "125.00" in out
    assert "18.00" in out
    assert "41.00" in out
    assert postprocess_pdf_text(text) == text


def test_chart_debris_still_removes_tick_row_runs() -> None:
    # Guard that the refine did not over-tighten: real tick-label rows (several
    # space-separated values per line) are still flushed as a run.
    text = "keep this line\n0 1 2 3 4 5 6\n1 2 3\n4 5 6\nkeep too"
    out = strip_chart_text_debris(text)
    assert "keep this line" in out
    assert "keep too" in out
    assert "0 1 2 3 4 5 6" not in out
    assert "1 2 3" not in out


# ---------------------------------------------------------------------------
# Integration: pymupdf-generated bordered/borderless tables (self-contained,
# do not depend on the committed samples dir being present).
# ---------------------------------------------------------------------------
def _make_table_with_text(with_borders: bool) -> bytes:
    """Build a table PDF whose numeric Price/Total columns form trailing
    consecutive decimal lines in the layout reconstruction — the exact
    regression trigger."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=350)
    cols = {"id": 62, "name": 140, "price": 296, "total": 374}
    rows = [
        ("A-1001", "Notebook", "12.50", "125.00"),
        ("A-1002", "Gel-Pen", "1.80", "36.00"),
        ("A-1003", "Folder", "8.20", "41.00"),
        ("A-1004", "Stapler", "15.00", "30.00"),
        ("A-1005", "Labels", "0.60", "18.00"),
    ]
    size = 12
    y = 100
    for name, x in cols.items():
        page.insert_text((x, y), name.title(), fontname="helv", fontsize=size)
    y += 18
    for r in rows:
        for (x, val) in zip(cols.values(), r):
            page.insert_text((x, y), val, fontname="helv", fontsize=size)
        y += 18
    if with_borders:
        page.draw_line((60, 100), (415, 100), color=(0, 0, 0), width=1)
        page.draw_line((60, y), (415, y), color=(0, 0, 0), width=1)
        for yy in range(100, y + 1, 18):
            page.draw_line((60, yy), (415, yy), color=(0, 0, 0), width=1)
    return doc.write()


@pytest.mark.parametrize("with_borders", [True, False], ids=["bordered", "borderless"])
def test_synthetic_table_numeric_cells_survive_full_pipeline(with_borders: bool) -> None:
    content = _make_table_with_text(with_borders)
    doc = Parser().parse_file(file_name="table.pdf", file_type="pdf", content=content)
    for cell in ("12.50", "125.00", "18.00", "41.00"):
        assert cell in doc.content


# ---------------------------------------------------------------------------
# Integration: committed baseline fixtures (skipped if samples absent).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sample", sorted(_REAL_EXPECTATIONS))
def test_real_fixture_numeric_cells_survive(sample: str) -> None:
    path = _SAMPLES / sample
    if not path.is_file():
        pytest.skip(f"fixture not present: {path}")
    doc = Parser().parse_file(
        file_name=sample, file_type="pdf", content=path.read_bytes(),
    )
    for cell in _REAL_EXPECTATIONS[sample]:
        assert cell in doc.content