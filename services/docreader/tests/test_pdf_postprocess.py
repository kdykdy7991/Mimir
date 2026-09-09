"""Tests for PDF text post-processing (sanitize + noise cleanup), ported from
WeKnora pdf_parser verbatim rules."""

from __future__ import annotations

from docreader.parser.pdf_postprocess import (
    postprocess_pdf_text,
    sanitize_pdf_text,
    strip_arxiv_and_page_num_lines,
    strip_chart_text_debris,
    strip_lines_above_figure_captions,
)


def test_sanitize_removes_placeholders_and_joins() -> None:
    assert sanitize_pdf_text("hy\ufffephen") == "hyphen"
    assert sanitize_pdf_text("a\u00adb") == "ab"
    assert sanitize_pdf_text("clean text") == "clean text"


def test_strip_arxiv_and_page_nums() -> None:
    text = "arXiv:2301.00001v2 [cs.CL]\nPage 5\nbody starts\n3\nnext body\narXiv: no"
    out = strip_arxiv_and_page_num_lines(text)
    assert "arXiv:" not in out
    # a pure 1-3 digit page number line is removed; "Page 5" (its own page label) is kept
    assert "Page 5" in out
    assert "body starts" in out
    assert "next body" in out
    assert "3" not in [ln.strip() for ln in out.splitlines()]


def test_strip_chart_debris_removes_numeric_runs() -> None:
    text = "keep this line\n0 1 2 3 4 5 6\n1 2 3\n4 5 6\nkeep too"
    out = strip_chart_text_debris(text)
    assert "keep this line" in out
    assert "keep too" in out
    # a run of 3+ numeric/tick lines is removed
    assert "0 1 2 3 4 5 6" not in out
    assert "1 2 3" not in out


def test_strip_lines_above_figure_captions() -> None:
    text = "Figure 1: results chart\nsome label x\nFigure 2: second\nbody paragraph here\n"
    out = strip_lines_above_figure_captions(text)
    # "some label x" sits above "Figure 2" and is short -> removed
    assert "some label x" not in out
    assert "Figure 2: second" in out
    assert "Figure 1" in out


def test_postprocess_pipeline() -> None:
    text = "Figures model  \n..notes..\narXiv:2301.5\n\n0 1 2 3\nintro paragraph of reasonable length"
    out = postprocess_pdf_text(text)
    assert "intro paragraph" in out
    assert "arXiv:" not in out