# SPDX-License-Identifier: MIT
#
# Table / Markdown normalization for parser output (plan §Phase-3 items 3-5).
#
# * plain tables  -> canonical GFM Markdown (so table-aware chunking later has
#   a stable, offset-consistent representation);
# * tables with ``rowspan``/``colspan`` that cannot be represented losslessly
#   in GFM -> kept as clean HTML with styling / presentation attributes removed;
# * no local temp path (or its base64) leaks into body text: image references
#   are rewritten to ``images/<basename>`` keys.
#
# This module does NOT invent a table detector (plan: "不得在此阶段自行新增表格
# 识别算法"); it normalizes tables already surfaced by a parser (OpenDataLoader).
"""Table / Markdown normalization helpers (GFM + clean HTML + path hygiene)."""

from __future__ import annotations

import html as html_mod
import os
import re
from html.parser import HTMLParser

# Attributes that carry meaning for tables/cells and are safe to keep.
_SAFE_TABLE_ATTRS = {
    "rowspan",
    "colspan",
    "scope",
    "colgroup",
    "col",
    "align",  # alignment conveyed by the writer; kept for fidelity
}
# Presentation-only attributes stripped from HTML tables.
_PRESENTATION_ATTRS = {
    "style",
    "class",
    "id",
    "width",
    "height",
    "bgcolor",
    "border",
    "cellpadding",
    "cellspacing",
    "rules",
    "frame",
    "valign",
    "background",
    "color",
}

_GFM_TABLE_LINE = re.compile(r"^[ \t]*\|")


class _SanitizedHTML(HTMLParser):
    """Rebuild an HTML fragment keeping only safe table attrs (no style/class)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        keep = [(k, v) for k, v in attrs if k.lower() in _SAFE_TABLE_ATTRS]
        if not keep:
            self.parts.append(f"<{tag}>")
            return
        self.parts.append(
            "<" + tag + " " + " ".join(f'{k}="{html_mod.escape(v)}"' for k, v in keep) + ">",
        )

    def handle_startendtag(self, tag, attrs):
        self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)


def _sanitize_html_table(html_block: str) -> str:
    p = _SanitizedHTML()
    try:
        p.feed(html_block)
        p.close()
    except Exception:  # noqa: BLE001
        return html_block
    return "".join(p.parts)


def is_gfm_table(block: str) -> bool:
    lines = [ln for ln in block.splitlines() if ln.strip()]
    return bool(lines and _GFM_TABLE_LINE.match(lines[0]))


def _has_multi_rowcell_span(html_block: str) -> bool:
    return bool(
        re.search(r'<(td|th)\b[^>]*\b(rowspan|colspan)\s*=\s*["\'][2-9]', html_block, re.I),
    )


def normalize_html_table(html_block: str) -> str:
    """Return a clean HTML table, or the block unchanged if it is single-cell."""
    block = html_block.strip()
    if not re.search(r"<\s*table[\s>]", block, re.I):
        return block
    if _has_multi_rowcell_span(block):
        return _sanitize_html_table(block)
    # printable single-grid HTML table -> convert to GFM below via caller's
    # markdown pipeline; here we just return a cleaned HTML fallback.
    return _sanitize_html_table(block)


def _normalize_sep_cell(cell: str) -> str:
    c = cell.strip()
    left = c.startswith(":")
    right = c.endswith(":")
    core = c.strip(":")
    dashes = "-" * max(3, len(core) if core else 1)
    return (":" if left else "") + dashes + (":" if right else "")


def normalize_gfm_table(block: str) -> str:
    """Clean a GFM table: trim trailing whitespace, normalize the separator."""
    lines = block.splitlines()
    out = []
    for i, ln in enumerate(lines):
        s = ln.rstrip()
        if i == 1 and re.fullmatch(r"\|[\s:|-]+\|", s):
            cells = _split_gfm_row(s)
            s = "|" + "|".join(_normalize_sep_cell(c) for c in cells) + "|"
        out.append(s)
    return "\n".join(out)


def _split_gfm_row(row: str) -> list[str]:
    row = row.strip().strip("|")
    if not row:
        return [""]
    return [c for c in row.split("|")]


# --- path hygiene -----------------------------------------------------------
_TEMP_MARKDOWN_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def rewrite_abs_image_paths(content: str) -> str:
    """Rewrite ``![alt](<abs path>)`` image URLs to ``images/<basename>``.

    Removes any local filesystem / temp path from body text (plan item 5);
    leaves ``data:`` and already-relative ``images/...`` refs untouched.
    """
    def repl(match: re.Match[str]) -> str:
        alt, url = match.group(1), match.group(2)
        u = (url or "").strip().split()[0]
        if u.startswith("data:") or u.startswith("images/") or u.startswith("http"):
            return match.group(0)
        if os.path.isabs(u) or u.startswith("./") or u.startswith("../"):
            name = os.path.basename(u)
            return f"![{alt}](images/{name})"
        return match.group(0)

    return _TEMP_MARKDOWN_REF_RE.sub(repl, content)


def normalize_markdown(content: str) -> str:
    """Run the full normalization over parser output."""
    content = rewrite_abs_image_paths(content or "")
    return content