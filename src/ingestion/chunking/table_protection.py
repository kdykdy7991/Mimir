"""
Table-aware span extraction for chunk protection (Phase 4).

``find_table_spans`` scans a document's text for protected table blocks —
GFM Markdown tables and complete HTML ``<table>`` elements — so the chunker
can split around them (ordinary boundaries never cut a table) and protect
table header context across row-split parts.

This is scope-gated to *recognising already-present table blocks*; it does not
invent new table formats beyond the two the pipeline produces (GFM via
table_normalize, clean HTML for rowspan/colspan tables).

Design notes (mirrors the plan):
* each span carries the exact original ``start``/``end`` offsets so chunk
  offsets in the body stay truthful (no fabricated offsets).
* ``split_table`` keeps the header in every part so a cross-chunk big table can
  still be read column-to-column on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Default max protected length (chars) for a single atomic table chunk.
DEFAULT_MAX_PROTECTED = 7500

_GFM_HEADER = re.compile(r"^\s*\|")
_GFM_SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_HTML_TABLE_OPEN = re.compile(r"<\s*table[\s>]", re.IGNORECASE)
_HTML_TABLE_CLOSE = re.compile(r"</\s*table\s*>", re.IGNORECASE)


@dataclass
class TableSpan:
    """A protected table block within the document text."""
    text: str
    start: int
    end: int
    kind: str = "gfm"  # "gfm" | "html"
    header: str = ""   # header row text (for context_header / row-split)
    index: int = -1    # reading-order index of this table
    parts: list[str] = field(default_factory=list)  # filled by split_table

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"TableSpan(kind={self.kind}, start={self.start}, end={self.end}, hdr={self.header!r})"


def _gfm_header_row(block: str) -> str:
    first = block.splitlines()[0] if block.splitlines() else ""
    return first.strip().strip("|").strip() if first else ""


def _find_gfm_table(lines: list[str], line_starts: list[int], i: int, text: str) -> TableSpan | None:
    # lines[i] is a '|' line; a table needs a separator on lines[i+1].
    if i + 1 >= len(lines) or not _GFM_SEPARATOR.fullmatch(lines[i + 1].strip()):
        return None
    start = line_starts[i]
    # data rows begin at i+2; extend while they look like GFM rows
    j = i + 2
    while j < len(lines) and lines[j].strip() and lines[j].lstrip().startswith("|"):
        j += 1
    # j - 1 is the last data-row (or the separator line if no rows)
    last = max(i + 1, j - 1)
    end_last = line_starts[last] + len(lines[last].rstrip("\n"))
    end = end_last
    # include a single trailing blank line as part of the protected block
    if j < len(lines) and not lines[j].strip() and line_starts[j] >= end_last:
        end = line_starts[j]
    block = text[start:end].rstrip("\n")
    return TableSpan(
        text=block,
        start=start,
        end=end,
        kind="gfm",
        header=_gfm_header_row(block),
    )


def find_table_spans(text: str, max_len: int = DEFAULT_MAX_PROTECTED) -> list[TableSpan]:
    """Return protected table spans in reading order, merged over overlaps.

    Merges a GFM span that touches an HTML span (or vice-versa) into one, so
    we never split a boundary before a table's trailing image/annotation.
    """
    lines = text.splitlines(keepends=True)
    line_starts = []
    acc = 0
    for ln in lines:
        line_starts.append(acc)
        acc += len(ln)
    spans: list[TableSpan] = []
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        hm = _HTML_TABLE_OPEN.search(lines[i]) if ln else None
        if ln.startswith("|") and _GFM_HEADER.match(ln):
            g = _find_gfm_table(lines, line_starts, i, text)
            if g:
                if spans and spans[-1].end >= g.start:
                    spans[-1].end = max(spans[-1].end, g.end)
                    spans[-1].text = text[spans[-1].start:spans[-1].end].rstrip("\n")
                else:
                    spans.append(g)
                i = _strict_advance(lines, line_starts, spans[-1].end, i)
                continue
        if hm:
            rest = text[line_starts[i]:]
            m = _HTML_TABLE_CLOSE.search(rest)
            if m:
                block_len = m.end()
                g = TableSpan(
                    text=text[line_starts[i]:line_starts[i] + block_len].rstrip("\n"),
                    start=line_starts[i],
                    end=line_starts[i] + block_len,
                    kind="html",
                )
                if spans and spans[-1].end >= g.start:
                    spans[-1].end = max(spans[-1].end, g.end)
                    spans[-1].text = text[spans[-1].start:spans[-1].end].rstrip("\n")
                else:
                    spans.append(g)
                i = _strict_advance(lines, line_starts, spans[-1].end, i)
                continue
        i += 1

    # number tables in reading order
    for idx, s in enumerate(spans):
        s.index = idx
    return spans


def index_at(lines: list[str], line_starts: list[int], char_index: int) -> int:
    """Return the line index whose start <= char_index (binary searchish)."""
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi) // 2
        if line_starts[mid] <= char_index:
            lo = mid + 1
        else:
            hi = mid
    return max(lo - 1, 0)


def _strict_advance(lines: list[str], line_starts: list[int], char_index: int, current: int) -> int:
    """Advance past a consumed block, guaranteeing forward progress.

    ``index_at`` can return the *same* line when a span ends inside the current
    line (e.g. a single-line HTML table). Silently staying put would loop.
    """
    ni = index_at(lines, line_starts, char_index)
    return ni if ni > current else current + 1


def split_gfm_table(block: str, max_len: int) -> list[str]:
    """Split a GFM table by complete rows; header+separator repeated per part."""
    raw = block.splitlines()
    if len(raw) < 2:
        return [block]
    header, sep = raw[0], raw[1]
    rows = raw[2:]
    parts: list[str] = []
    cur = [header, sep]
    cur_len = len("\n".join(cur)) + 1
    for row in rows:
        cand_len = cur_len + len(row) + 1
        if cand_len > max_len and len(cur) > 2:
            parts.append("\n".join(cur))
            cur = [header, sep, row]
            cur_len = len("\n".join(cur)) + 1
        else:
            cur.append(row)
            cur_len = cand_len
    if cur:
        parts.append("\n".join(cur))
    return parts


def split_html_table(block: str, max_len: int) -> list[str]:
    """Split an HTML table by complete ``<tr>``; header row kept per part."""
    if len(block) <= max_len:
        return [block]
    m_open = _HTML_TABLE_OPEN.search(block)
    m_close = _HTML_TABLE_CLOSE.search(block)
    if not m_open or not m_close:
        return [block]
    prefix = block[: m_open.start()]
    rows = list(re.finditer(r"<tr[^>]*>.*?</tr>", block, re.IGNORECASE | re.DOTALL))
    header_row = rows[0].group(0) if rows else ""
    rows = rows[1:]

    parts = []
    cur_rows: list[str] = []

    def flush() -> None:
        parts.append(prefix + "<table>" + header_row + "".join(cur_rows) + "</table>")

    for r in rows:
        cand = prefix + "<table>" + header_row + "".join(cur_rows + [r.group(0)]) + "</table>"
        if len(cand) > max_len and cur_rows:
            flush()
            cur_rows = [r.group(0)]
        else:
            cur_rows.append(r.group(0))
    if cur_rows:
        flush()
    return parts


def split_table(span: TableSpan, max_len: int = DEFAULT_MAX_PROTECTED) -> list[str]:
    """Return 1..N parts for the span; atomic if single-part (<= max_len)."""
    if span.kind == "html":
        return split_html_table(span.text, max_len)
    return split_gfm_table(span.text, max_len)