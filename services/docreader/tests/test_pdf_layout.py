"""Tests for layout-aware multi-column text extraction (pymupdf backend).

Verifies that two physical columns are linearised column-by-column (not line-
interleaved), that headings are promoted, and that a broken reconstruction
falls back to plain text.
"""

from __future__ import annotations

import pymupdf

from docreader.parser import pdf_layout as L


def _two_col_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=600)
    # Short lines so each stays inside its half of the page → a clean gutter.
    left = ["left line one", "left line two", "left line three"]
    right = ["right line one", "right line two", "right line three"]
    for i in range(3):
        page.insert_text((50, 100 + i * 40), left[i])
        page.insert_text((260, 110 + i * 40), right[i])
    data = doc.tobytes()
    doc.close()
    return data


def test_two_columns_linearised_column_first() -> None:
    pdf = pymupdf.open(stream=_two_col_pdf(), filetype="pdf")
    page = pdf.load_page(0)
    text = L.extract_layout_text(page)
    left_pos = text.find("left line")
    right_pos = text.find("right line")
    assert left_pos != -1 and right_pos != -1
    # every "left line N" should precede every "right line N" (column-first)
    assert text.find("right line one") > text.find("left line three") or (
        right_pos > left_pos
    )
    # left block must contain all three left lines before any right line
    left_block_ok = (
        text.find("left line one")
        < text.find("left line two")
        < text.find("left line three")
    )
    assert left_block_ok
    first_right = min(text.find(x) for x in ("right line one", "right line two", "right line three"))
    assert first_right > text.find("left line three"), (
        "columns must be linearised, not line-interleaved"
    )


def test_plain_vs_layout_prefer_plain_for_toc_like() -> None:
    plain = "Introduction\nThis is a fairly long line that is well formed with words.\n"
    layout = "###  Intro\nduc tion\n####  Lead\n"
    # layout is heavily single/garbled -> should prefer plain
    assert L.should_prefer_plain(plain, layout) is True
    assert L.should_prefer_plain(plain, "") is True


def test_pure_column_split_drops_narrow_margin() -> None:
    # two wide columns + one 1-char narrow margin strip
    col_a = [{"x0": 50 + i, "y0": 0, "x1": 52 + i, "y1": 10, "ch": "a"} for i in range(100)]
    col_b = [{"x0": 250 + i, "y0": 0, "x1": 252 + i, "y1": 10, "ch": "b"} for i in range(100)]
    margin = [{"x0": 5, "y0": i * 12, "x1": 6, "y1": i * 12 + 10, "ch": "z"} for i in range(30)]
    chars = col_a + col_b + margin
    cols = L.filter_reading_columns(chars, scale=10.0, width=420)
    spans = [L.column_x_span(c) for c in cols]
    # two wide streaks survive; the narrow margin strip is dropped
    assert len(cols) == 2
    assert all(s > 60 for s in spans)
    assert not any(L.column_x_span(c) < 5 for c in cols)