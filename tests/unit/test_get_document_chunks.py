"""
P3.3/P3.4 — ``get_document_chunks`` tool handler tests.
"""

from __future__ import annotations

from src.mcp_server.clients.models import DocumentChunk, DocumentChunkPage
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import get_document_chunks as gdc


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _page(id_="u1"):
    return DocumentChunkPage(
        document_id=id_, page=1, page_size=2, total=4,
        chunks=[
            DocumentChunk(chunk_id=f"{id_}_{i:04d}_h", index=i,
                          text=f"chunk {i}", page=(i + 1) % 3 or None,
                          section="intro" if i == 0 else "")
            for i in range(2)
        ],
    )


class FakeClient:
    def __init__(self): self.calls = []
    def get_document_chunks(self, document_id, page, page_size, principal):
        self.calls.append((document_id, page, page_size))
        return _page(document_id)


def test_missing_id_rejected():
    result = _run(gdc._get_document_chunks({}))
    assert result.is_error
    assert "document_id" in result.content[0].text


def test_document_id_and_doc_id_both_route():
    fake = FakeClient()
    _run(gdc._get_document_chunks(
        {"document_id": "u1", "page": 1, "page_size": 2, "_client": fake},
    ))
    _run(gdc._get_document_chunks(
        {"doc_id": "u2", "page": 1, "page_size": 2, "_client": fake},
    ))
    assert fake.calls == [("u1", 1, 2), ("u2", 1, 2)]


def test_defaults_page_and_page_size():
    fake = FakeClient()
    _run(gdc._get_document_chunks({"document_id": "u1", "_client": fake}))
    assert fake.calls == [("u1", 1, 20)]


def test_renders_structured_and_markdown():
    fake = FakeClient()
    md, structured = _run(gdc._get_document_chunks(
        {"document_id": "u1", "page": 1, "page_size": 2, "_client": fake},
    ))
    assert structured["document_id"] == "u1"
    assert structured["total"] == 4
    assert structured["has_next"] is True
    assert structured["chunks"][0]["chunk_id"] == "u1_0000_h"
    assert structured["chunks"][0]["index"] == 0
    assert "## Document chunks" not in md and "# Document chunks" in md


def test_register_adds_tool():
    h = ProtocolHandler()
    gdc.register(h)
    assert h.has("get_document_chunks")


def test_bad_int_params_rejected():
    result = _run(gdc._get_document_chunks({"document_id": "u1", "page_size": "xx"}))
    assert result.is_error