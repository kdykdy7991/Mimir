"""
Unit tests for ``get_document_summary`` tool (E5).

Covers:
- Empty / missing doc_id → CallToolResult(is_error=True)
- Document not found → CallToolResult(is_error=True) with a clear message
- Document found → structured payload + markdown rendering
- Source path / page / tags flow through correctly
- Missing optional fields default to safe placeholders

Error convention (MCP 2.0): *known* parameter / business errors return a
``CallToolResult`` with ``is_error=True``; unexpected exceptions still
propagate (→ protocol MCPError). See protocol_handler "Error mapping".
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from src.mcp_server.tools import get_document_summary as gds


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_empty_doc_id_rejected(tmp_path: Path):
    result = _run(gds._get_document_summary({
        "doc_id": "  ",
        "_config_path": str(tmp_path / "no.yaml"),
        "_data_dir": str(tmp_path),
    }))
    assert result.is_error
    assert "doc_id" in result.content[0].text


def test_missing_doc_id_rejected(tmp_path: Path):
    result = _run(gds._get_document_summary({
        "_config_path": str(tmp_path / "no.yaml"),
        "_data_dir": str(tmp_path),
    }))
    assert result.is_error
    assert "doc_id" in result.content[0].text


# ---------------------------------------------------------------------------
# Not found
# ---------------------------------------------------------------------------

def test_not_found_raises_clear_error(tmp_path: Path):
    # Valid UUID that has no ingestion-history record → not found.
    with patch.object(gds, "_resolve_doc", return_value=None):
        result = _run(gds._get_document_summary({
            "doc_id": str(UUID("0" * 32)),
            "_config_path": str(tmp_path / "no.yaml"),
            "_data_dir": str(tmp_path),
        }))
    assert result.is_error
    assert "document not found" in result.content[0].text


def test_non_uuid_doc_id_reports_not_found(tmp_path: Path):
    """A non-UUID doc_id can never resolve → same not-found contract as
    the Web API (it never matches any derived document UUID)."""
    with patch.object(gds, "_resolve_doc", return_value=None):
        result = _run(gds._get_document_summary({
            "doc_id": "definitely-not-a-real-id",
            "_config_path": str(tmp_path / "no.yaml"),
            "_data_dir": str(tmp_path),
        }))
    assert result.is_error
    assert "document not found" in result.content[0].text


# ---------------------------------------------------------------------------
# Found — structured payload
# ---------------------------------------------------------------------------

def test_found_returns_structured_payload(tmp_path: Path):
    hits = [
        {
            "id": "doc1_0000_abcdef",
            "text": "body text of first chunk",
            "metadata": {
                "title": "My Document",
                "summary": "A short summary.",
                "tags": ["alpha", "beta"],
                "source_path": "/abs/path/doc.pdf",
                "doc_type": "pdf",
                "page_num": 3,
            },
        },
        {
            "id": "doc1_0001_12345678",
            "text": "body text of second chunk",
            "metadata": {"title": "My Document", "source_path": "/abs/path/doc.pdf"},
        },
    ]
    doc_id = str(UUID("1" * 32))
    fake_router = FakeRouter(hits)
    with patch.object(
        gds, "_resolve_doc", return_value=("knowledge-base", "/abs/path/doc.pdf"),
    ), patch_vector_store_multi(fake_router):
        md, structured = _run(gds._get_document_summary({
            "doc_id": doc_id,
            "_config_path": str(tmp_path / "no.yaml"),
            "_data_dir": str(tmp_path),
        }))

    assert structured["doc_id"] == doc_id
    assert structured["title"] == "My Document"
    assert structured["summary"] == "A short summary."
    assert structured["tags"] == ["alpha", "beta"]
    assert structured["source_path"] == "/abs/path/doc.pdf"
    assert structured["doc_type"] == "pdf"
    assert structured["chunk_count"] == 2

    # It must scope the lookup to the resolved collection.
    assert fake_router.calls
    assert fake_router.calls[0]["collection"] == "knowledge-base"
    assert fake_router.calls[0]["filters"] == {"source_path": "/abs/path/doc.pdf"}

    # Markdown rendering.
    assert "My Document" in md
    assert "knowledge-base" not in md  # doc_id (UUID) is shown, not the collection
    assert "## Summary" in md
    assert "A short summary." in md


def test_missing_optional_fields_use_placeholders(tmp_path: Path):
    # Chunk metadata carries no title/summary/tags/doc_type. The resolved
    # source_path is still reported (it comes from the registry).
    hits = [
        {"id": "x", "text": "t", "metadata": {}},
    ]
    fake_router = FakeRouter(hits)
    with patch.object(
        gds, "_resolve_doc", return_value=("kb", "/docs/x.pdf"),
    ), patch_vector_store_multi(fake_router):
        _, structured = _run(gds._get_document_summary({
            "doc_id": str(UUID("2" * 32)),
            "_config_path": str(tmp_path / "no.yaml"),
            "_data_dir": str(tmp_path),
        }))
    assert structured["title"] == "(untitled)"
    assert structured["summary"] == ""
    assert structured["tags"] == []
    assert structured["source_path"] == "/docs/x.pdf"
    assert structured["doc_type"] == ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class FakeRouter:
    """Minimal stand-in for ``MultiCollectionVectorStore``: records the
    ``collection=`` kwarg and returns the configured hits."""

    def __init__(self, hits):
        self._hits = hits
        self.calls = []

    def get_by_metadata(self, filters, *, limit=None, **kwargs):
        self.calls.append({"filters": filters, "limit": limit, **kwargs})
        return self._hits


@contextmanager
def patch_vector_store_multi(fake_router):
    """Patch the VectorStoreFactory.create_multi_collection call inside gds."""
    from src.libs import vector_store as vs_mod

    orig = vs_mod.VectorStoreFactory.create_multi_collection
    vs_mod.VectorStoreFactory.create_multi_collection = staticmethod(
        lambda settings: fake_router,
    )
    try:
        yield fake_router
    finally:
        vs_mod.VectorStoreFactory.create_multi_collection = orig
