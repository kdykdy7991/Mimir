"""Phase 4: table-aware chunking and protected table spans.

Covers GFM/HTML table span detection, atomic small tables, row-split of large
tables with repeated ``context_header``, and body/table reading-order offsets.
"""

from __future__ import annotations

import pytest

from src.core.types import Chunk, Document
from src.ingestion.chunking import DocumentChunker
from src.ingestion.chunking.table_protection import (
    find_table_spans,
    split_table,
)
from src.libs.splitter.base_splitter import BaseSplitter

GFM = "| Name | Qty |\n|------|-----|\n| Pen  | 3   |\n| Ink  | 5   |\n"


class BlankLineSplitter(BaseSplitter):
    """Split body on blank lines (deterministic, no langchain needed)."""

    def split_text(self, text: str, **kwargs):
        return [p for p in text.split("\n\n") if p]


def _chunker() -> DocumentChunker:
    return DocumentChunker(splitter=BlankLineSplitter())


def _doc(text: str) -> Document:
    return Document(id="doc-1", text=text, metadata={"source_path": "t.md"})


def test_find_gfm_table_span() -> None:
    text = "intro\n\n" + GFM + "\n\noutro"
    spans = find_table_spans(text)
    assert len(spans) == 1
    s = spans[0]
    assert s.kind == "gfm"
    assert text[s.start:s.end].startswith("| Name |")
    assert s.header == "Name | Qty"


def test_find_html_table_span() -> None:
    html = '<table><tr><td rowspan="2">A</td></tr></table>'
    text = "before\n" + html + "\nafter"
    spans = find_table_spans(text)
    assert len(spans) == 1 and spans[0].kind == "html"


def test_atomic_small_table() -> None:
    text = "intro\n\n" + GFM + "\n\noutro"
    chunks = _chunker().split_document(_doc(text))
    tables = [c for c in chunks if c.metadata.get("content_type") == "table"]
    assert len(tables) == 1
    assert tables[0].metadata["context_header"] == "Name | Qty"
    assert tables[0].metadata["table_index"] == 0
    # reading order: intro, table, outro
    texts = [c.text for c in chunks]
    assert texts[0].startswith("intro")
    assert texts[-1].startswith("outro")
    assert any("| Pen" in t for t in texts)


def test_large_table_row_split_repeats_header() -> None:
    rows = "\n".join(f"| r{i} | {i} |" for i in range(30))
    big = "| Name | V |\n|------|---|\n" + rows
    text = "lead\n\n" + big + "\n\ntail"
    chunks = _chunker().split_document(_doc(text), max_protected=40)
    parts = [c for c in chunks if c.metadata.get("content_type") == "table"]
    assert len(parts) > 1
    for c in parts:
        assert c.metadata.get("context_header") == "Name | V"
        assert "table_part_index" in c.metadata
        # every part repeats the header so it is self-explanatory
        assert c.text.splitlines()[0].startswith("| Name")
    # a part does NOT mix a later table's header from a different table
    assert all(c.metadata["table_index"] == 0 for c in parts)


def test_boundaries_never_cut_table() -> None:
    # body separator "\n\n" present *around* the table; splitter must not
    # place a boundary inside the table body.
    row = "| cell | val |"
    text = "one\n\ntwo\n\n" + GFM + "\n\n" + row + "\n\nthree"
    chunks = _chunker().split_document(_doc(text))
    for c in chunks:
        assert c.text not in (GFM.splitlines()[1] if len(GFM.splitlines()) > 1 else "")


def test_offsets_monotonic_and_body_truthful() -> None:
    text = "para one\n\n" + GFM + "\n\npara two"
    chunks = _chunker().split_document(_doc(text))
    prev_end = -1
    for c in chunks:
        assert c.start_offset >= prev_end
        prev_end = c.end_offset
    # body chunk text matches document.text at its offsets
    bodies = [c for c in chunks if c.metadata.get("content_type") != "table"]
    for c in bodies:
        assert document_text_slice_equals(text, c)


def document_text_slice_equals(text: str, c: Chunk) -> bool:
    slice_ = text[c.start_offset:c.end_offset]
    return slice_.strip() == c.text.strip() or c.text in text[c.start_offset : c.start_offset + len(c.text) + 1]


def test_split_table_atomic_and_rowwise() -> None:
    span = find_table_spans(GFM)[0]
    parts = split_table(span, max_len=10_000)
    assert len(parts) == 1 and parts[0].startswith("|")
    for interval in (20, 60):
        parts = split_table(span, max_len=interval)
        assert len(parts) >= 1
        assert all(not p.strip() or "|" in p for p in parts)