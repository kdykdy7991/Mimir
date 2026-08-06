"""
Unit tests for response builder + citation generator (E3 helpers).

Covers:
- Citation generation: index, source path, page, score, source_type
- Empty result → friendly message (not empty string)
- Markdown references section uses [n] markers
- structured payload mirrors the citations list
- Excerpt truncation at the configured limit
"""

from __future__ import annotations

import pytest

from src.core.response import build_response
from src.core.response.citation_generator import generate
from src.core.types import ChunkRecord, RetrievalResult


def _result(
    cid: str,
    text: str,
    *,
    score: float = 0.5,
    page: int | None = None,
    source: str = "/path/to/doc.pdf",
    source_type: str = "fusion",
) -> RetrievalResult:
    return RetrievalResult(
        chunk=ChunkRecord(
            id=cid,
            text=text,
            metadata={"source_path": source, "page_num": page},
        ),
        score=score,
        rank=0,
        source=source_type,
    )


# ---------------------------------------------------------------------------
# Citation generator
# ---------------------------------------------------------------------------

def test_citation_index_is_1_based():
    rs = [_result("a", "alpha"), _result("b", "beta"), _result("c", "gamma")]
    cites = generate(rs)
    assert [c.index for c in cites] == [1, 2, 3]


def test_citation_carries_chunk_id_and_score():
    rs = [_result("c1", "text", score=0.91)]
    c = generate(rs)[0]
    assert c.chunk_id == "c1"
    assert c.score == pytest.approx(0.91)


def test_citation_carries_page_and_source():
    rs = [_result("c1", "text", page=7, source="/x.pdf")]
    c = generate(rs)[0]
    assert c.page == 7
    assert c.source == "/x.pdf"


def test_citation_carries_source_type():
    rs = [_result("c1", "text", source_type="rerank")]
    c = generate(rs)[0]
    assert c.source_type == "rerank"


def test_citation_handles_missing_metadata():
    rs = [
        RetrievalResult(
            chunk=ChunkRecord(id="x", text="y", metadata={}),
            score=0.1, rank=0, source="",
        ),
    ]
    c = generate(rs)[0]
    assert c.source == "(no source)"
    assert c.page is None


def test_citation_excerpt_truncated():
    long = "x" * 500
    rs = [_result("c1", long)]
    c = generate(rs)[0]
    assert len(c.text_excerpt) <= 200
    assert c.text_excerpt.endswith("…")


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def test_response_empty_returns_friendly_markdown():
    resp = build_response([], "any query")
    assert "未找到相关文档" in resp.markdown
    assert resp.structured["n_results"] == 0
    assert resp.structured["citations"] == []
    assert resp.structured["query"] == "any query"


def test_response_marks_references_section():
    rs = [_result("a", "alpha", source="/x.pdf", page=1)]
    resp = build_response(rs, "q")
    assert "## References" in resp.markdown
    assert "[1]" in resp.markdown
    assert "/x.pdf" in resp.markdown


def test_response_includes_excerpts_in_body():
    rs = [_result("a", "alpha-text"), _result("b", "beta-text")]
    resp = build_response(rs, "q")
    assert "alpha-text" in resp.markdown
    assert "beta-text" in resp.markdown


def test_response_structured_citations_in_order():
    rs = [
        _result("a", "alpha", score=0.9),
        _result("b", "beta", score=0.5),
    ]
    resp = build_response(rs, "q")
    cites = resp.structured["citations"]
    assert cites[0]["index"] == 1
    assert cites[0]["chunk_id"] == "a"
    assert cites[1]["index"] == 2
    assert cites[1]["chunk_id"] == "b"


def test_response_as_pair_shape():
    """The (str, dict) pair shape is what ProtocolHandler consumes."""
    resp = build_response([_result("a", "alpha")], "q")
    md, structured = resp.as_pair()
    assert isinstance(md, str)
    assert isinstance(structured, dict)
    assert structured["n_results"] == 1