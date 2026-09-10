"""
P2.3 — ``get_document`` canonical tool (shared handler).

Covers: document_id / doc_id resolution, unified not-found & denied
message, rendering of canonical + legacy output fields, and registration.
"""

from __future__ import annotations

from src.mcp_server.clients.errors import (
    AccessDeniedError,
    ResourceNotFoundError,
)
from src.mcp_server.clients.models import DocumentInfo
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import get_document as gd


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_missing_id_rejected():
    result = _run(gd._get_document_item({"doc_id": ""}))
    assert result.is_error
    assert "document_id" in result.content[0].text


def test_document_id_canonical_and_doc_id_alias_both_work():
    class FakeClient:
        def __init__(self): self.seen = []
        def get_document(self, document_id, principal):
            self.seen.append(document_id)
            return DocumentInfo(document_id=document_id, collection="kb", title="T", chunk_count=1)
    fake = FakeClient()
    _run(gd._get_document_item({"document_id": "u1", "_client": fake}))
    _run(gd._get_document_item({"doc_id": "u2", "_client": fake}))
    assert fake.seen == ["u1", "u2"]


def test_not_found_and_denied_are_unified_message():
    class NotFoundClient:
        def get_document(self, document_id, principal):
            raise ResourceNotFoundError("document not found")
    class DeniedClient:
        def get_document(self, document_id, principal):
            raise AccessDeniedError("denied")

    nf = _run(gd._get_document_item({"document_id": "u-id", "_client": NotFoundClient()}))
    dn = _run(gd._get_document_item({"document_id": "u-id", "_client": DeniedClient()}))
    assert nf.is_error and dn.is_error
    # Identical, non-revealing message — no presence leakage.
    assert nf.content[0].text == "document not found or not accessible"
    assert dn.content[0].text == "document not found or not accessible"
    assert "/x.pdf" not in nf.content[0].text


def test_render_exposes_canonical_and_legacy_fields():
    class FakeClient:
        def get_document(self, document_id, principal):
            return DocumentInfo(
                document_id=document_id, collection="kb",
                title="Doc", document_type="pdf", source="/a.pdf",
                summary="sum", tags=["t"], chunk_count=3,
            )
    md, structured = _run(gd._get_document_item(
        {"document_id": "uuid-1", "_client": FakeClient()},
    ))
    assert structured["document_id"] == "uuid-1"
    assert structured["collection"] == "kb"
    assert structured["document_type"] == "pdf"
    assert structured["source"] == "/a.pdf"
    # Legacy aliases
    assert structured["doc_id"] == "uuid-1"
    assert structured["doc_type"] == "pdf"
    assert structured["source_path"] == "/a.pdf"
    assert structured["chunk_count"] == 3
    assert "Doc" in md and "## Summary" in md


def test_requiring_document_id_or_doc_id():
    h = ProtocolHandler()
    gd.register(h)
    schema = h.get("get_document").input_schema
    assert "document_id" in schema["properties"]
    assert "doc_id" in schema["properties"]
    assert schema["required"] == ["document_id"]


def test_register_adds_get_document():
    h = ProtocolHandler()
    gd.register(h)
    assert h.has("get_document")