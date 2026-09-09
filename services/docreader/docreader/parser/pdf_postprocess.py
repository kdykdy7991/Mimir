# SPDX-License-Identifier: MIT
#
# Adapted from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/parser/pdf_parser.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# Verbatim port of the pure text-postprocessing helpers: PDF text-layer
# artifact repair, arXiv/page-number line removal, chart/axis debris stripping,
# and cleaning label lines above Figure captions.
"""PDF text-layer post-processing (sanitize + noise cleanup)."""

from __future__ import annotations

import os
import re

SANITIZE_PDF_TEXT = os.environ.get("DOCREADER_PDF_SANITIZE_TEXT", "1") in {
    "1", "true", "yes", "y", "on",
}
STRIP_CHART_TEXT_DEBRIS = os.environ.get("DOCREADER_PDF_STRIP_CHART_DEBRIS", "1") in {
    "1", "true", "yes", "y", "on",
}

# pdfium / Adobe text layers often emit U+FFFE for missing hyphens/ligatures.
# (pymupdf emits the same placeholder silently.)
_PDF_ARTIFACT_RE = re.compile(r"[\u00ad\u200b-\u200f\ufeff\ufffe\uffff]")
_PDF_ARTIFACT_JOIN_RE = re.compile(r"(\w)[\u00ad\ufffe](\w)")
_CHART_DEBRIS_LINE_RE = re.compile(
    r"^(?:"
    r"[\d\s.]+|"
    r"\d{1,2}|"
    r"\d+-layer|"
    r"iter\.\s*\(1e4\)|"
    r"(?:training|test)\s+error\s*\(%\)"
    r")$",
    re.IGNORECASE,
)
_CHART_LAYER_RE = re.compile(r"^\d+-layer$", re.IGNORECASE)
_FIGURE_CAPTION_RE = re.compile(r"^Figure\s+\d+\b", re.IGNORECASE)
_FIGURE_CAPTION_SEARCH_RE = re.compile(r"\bFigure\s+(\d+)\b", re.IGNORECASE)
_ARXIV_LINE_RE = re.compile(r"^arXiv:\s*\S+", re.IGNORECASE)
_PAGE_NUM_LINE_RE = re.compile(r"^\d{1,3}$")


def sanitize_pdf_text(text: str) -> str:
    """Remove PDF text-layer placeholders and repair broken hyphenations."""
    if not text:
        return text
    text = _PDF_ARTIFACT_RE.sub("", text)
    text = _PDF_ARTIFACT_JOIN_RE.sub(r"\1\2", text)
    return text


def _is_chart_debris_line(line: str) -> bool:
    t = line.strip()
    if not t:
        return False
    if _CHART_DEBRIS_LINE_RE.match(t):
        return True
    if _CHART_LAYER_RE.match(t):
        return True
    # Tick labels like "0 1 2 3 4 5 6 0"
    if re.fullmatch(r"[\d\s.()-]+", t) and len(t) <= 24 and sum(c.isdigit() for c in t) >= 3:
        return True
    return False


def strip_chart_text_debris(text: str) -> str:
    """Drop runs of axis/legend lines leaked from vector figures."""
    if not text:
        return text
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list = []
    i = 0
    while i < len(lines):
        if _is_chart_debris_line(lines[i]):
            j = i
            while j < len(lines) and (
                _is_chart_debris_line(lines[j]) or not lines[j].strip()
            ):
                j += 1
            if j - i >= 3:
                i = j
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def strip_arxiv_and_page_num_lines(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list = []
    for ln in lines:
        t = ln.strip()
        if _ARXIV_LINE_RE.match(t):
            continue
        if _PAGE_NUM_LINE_RE.match(t):
            continue
        if "arXiv:" in ln:
            ln = re.sub(r"\s*arXiv:\s*\S+\s*(?:\[[^\]]+\])?\s*[^\n]*", "", ln).strip()
            if not ln:
                continue
        kept.append(ln)
    return "\n".join(kept)


def line_has_figure_caption(text: str) -> bool:
    return bool(_FIGURE_CAPTION_SEARCH_RE.search((text or "").strip()))


def _is_body_paragraph_line(text: str) -> bool:
    t = text.strip()
    if len(t) < 48:
        return False
    return len(t.split()) >= 8


def _is_figure_interior_line(text: str) -> bool:
    """Short, non-body line directly above a Figure caption (diagram labels, ticks)."""
    t = text.strip()
    if not t or _FIGURE_CAPTION_RE.match(t):
        return False
    if _ARXIV_LINE_RE.match(t) or _PAGE_NUM_LINE_RE.match(t):
        return True
    if _is_body_paragraph_line(t):
        return False
    if _is_chart_debris_line(t):
        return True
    if t.endswith((".", "。", "!", "?", "！")) and len(t) >= 15:
        return False
    if len(t.split()) >= 7:
        return False
    if len(t) <= 40:
        return True
    return False


def strip_lines_above_figure_captions(text: str) -> str:
    """Remove diagram label lines that sit immediately above a Figure caption."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list = []
    for ln in lines:
        if line_has_figure_caption(ln):
            while out and _is_figure_interior_line(out[-1]):
                out.pop()
            out.append(ln)
        else:
            out.append(ln)
    return "\n".join(out)


def postprocess_pdf_text(text: str) -> str:
    if SANITIZE_PDF_TEXT:
        text = sanitize_pdf_text(text)
    text = strip_arxiv_and_page_num_lines(text)
    text = strip_lines_above_figure_captions(text)
    if STRIP_CHART_TEXT_DEBRIS:
        text = strip_chart_text_debris(text)
    return text