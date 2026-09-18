"""Task 03 contract tests for document discovery and exact chunk reads."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.mcp_server.auth.context import TrustedLocalPrincipal
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
from src.mcp_server.clients.models import (
    ChunkDetail,
    DocumentListRequest,
    DocumentPage,
    DocumentSummary,
)


class _Documents:
    def list_documents_paged(self, **kwargs):
        assert kwargs["collection"] == "kb"
        assert kwargs["offset"] == 2
        assert kwargs["limit"] == 2
        return ([SimpleNamespace(
            collection="kb", source_path="/private/report.pdf", status="success",
            n_chunks=4, n_images=1, created_at=10.0, updated_at=20.0,
            last_modified=15.0,
        )], 5)

    def list_document_keys(self, collection=None):
        return [("kb", "/private/report.pdf")]


def test_inprocess_list_documents_is_bounded_and_path_safe():
    services = SimpleNamespace(document=_Documents(), db=None)
    client = InProcessRagReadOnlyClient(services=services)
    result = client.list_documents(
        DocumentListRequest(collection="kb", page=2, page_size=2),
        TrustedLocalPrincipal(),
    )
    assert result.total == 5 and result.has_next is True
    assert result.documents[0].title == "report.pdf"
    assert result.documents[0].source == "report.pdf"
    assert "/private" not in result.documents[0].source


def test_get_chunk_checks_ownership_and_returns_neighbors():
    hits = [
        {"id": "c_0000_x", "text": "first", "metadata": {"chunk_index": 0}},
        {"id": "c_0001_x", "text": "body", "metadata": {
            "chunk_index": 1, "page_num": 3, "source_path": "/x/a.pdf",
            "content_type": "text", "asset_ids": ["asset-1"],
            "document_version": "dv", "chunk_version": "cv",
        }},
        {"id": "c_0002_x", "text": "last", "metadata": {"chunk_index": 2}},
    ]
    client = InProcessRagReadOnlyClient(data_dir="/tmp/unused")
    client._resolve_store_access = lambda document_id, principal: ("kb", "/x/a.pdf")
    client._read_document_chunks = lambda collection, source_path: hits
    item = client.get_chunk("doc", "c_0001_x", TrustedLocalPrincipal())
    assert item.text == "body"
    assert item.previous_chunk_id == "c_0000_x"
    assert item.next_chunk_id == "c_0002_x"
    assert item.source_locator == {"kind": "pdf_page", "page": 3}
    assert item.asset_ids == ["asset-1"]
    assert item.document_version == "dv" and item.chunk_version == "cv"
    assert item.is_current is True


def test_tools_render_new_primitives():
    from src.mcp_server.tools.get_chunk import _get_chunk
    from src.mcp_server.tools.list_documents import _list_documents

    class Client:
        def list_documents(self, request, principal):
            return DocumentPage(
                collection="kb", page=1, page_size=20, total=1,
                documents=[DocumentSummary(
                    document_id="doc", collection="kb", title="a.pdf",
                    source="a.pdf", chunk_count=1,
                )],
            )

        def get_chunk(self, document_id, chunk_id, principal):
            return ChunkDetail(
                document_id=document_id, chunk_id=chunk_id, index=0,
                text="evidence", source_locator={"kind": "none", "page": None},
                document_version="dv", chunk_version="cv",
            )

    _md, listed = asyncio.run(_list_documents({"collection": "kb", "_client": Client()}))
    _md, chunk = asyncio.run(_get_chunk({
        "document_id": "doc", "chunk_id": "chunk", "_client": Client(),
    }))
    assert listed["documents"][0]["document_id"] == "doc"
    assert chunk["text"] == "evidence"
    assert chunk["chunk_version"] == "cv" and chunk["is_current"] is True
    stale = asyncio.run(_get_chunk({
        "document_id": "doc", "chunk_id": "chunk",
        "expected_chunk_version": "old", "_client": Client(),
    }))
    assert stale.is_error is True
    assert "stale_reference" in stale.content[0].text
