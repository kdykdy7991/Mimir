"""
P1.2c — ``get_document_summary`` routed through the readonly client.

The handler only validates + formats; a fake client supplies the
``DocumentInfo`` or raises the unified not-found / denied errors. The
client's own resolve/auth/read logic is covered in
``tests/unit/test_readonly_client_document.py``.
"""

from __future__ import annotations

from src.mcp_server.clients.errors import (
    AccessDeniedError,
    ResourceNotFoundError,
)
from src.mcp_server.clients.models import DocumentInfo
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import get_document_summary as gds


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_empty_doc_id_rejected():
    result = _run(gds._get_document_summary({"doc_id": "  "}))
    assert result.is_error
    assert "doc_id" in result.content[0].text


def test_not_found_maps_to_legacy_message():
    class FakeClient:
        def get_document(self, document_id, principal):
            raise ResourceNotFoundError("document not found")

    result = _run(gds._get_document_summary(
        {"doc_id": "00000000-0000-0000-0000-000000000000", "_client": FakeClient()},
    ))
    assert result.is_error
    text = result.content[0].text
    assert "document not found" in text


def test_access_denied_maps_to_denied_message():
    class FakeClient:
        def get_document(self, document_id, principal):
            raise AccessDeniedError("document not found or not accessible")

    result = _run(gds._get_document_summary(
        {"doc_id": "10000000-0000-0000-0000-000000000000", "_client": FakeClient()},
    ))
    assert result.is_error
    assert result.content[0].text == "document not found or not accessible"


def test_found_renders_legacy_payload():
    class FakeClient:
        def get_document(self, document_id, principal):
            return DocumentInfo(
                document_id=document_id, collection="kb",
                title="My Document", document_type="pdf",
                source="/abs/doc.pdf", summary="A short summary.",
                tags=["alpha"], chunk_count=2,
            )

    md, structured = _run(gds._get_document_summary(
        {"doc_id": "12345678-0000-0000-0000-000000000000", "_client": FakeClient()},
    ))
    assert structured["doc_id"] == "12345678-0000-0000-0000-000000000000"
    assert structured["title"] == "My Document"
    assert structured["summary"] == "A short summary."
    assert structured["tags"] == ["alpha"]
    assert structured["source_path"] == "/abs/doc.pdf"
    assert structured["doc_type"] == "pdf"
    assert structured["chunk_count"] == 2
    assert "My Document" in md
    assert "## Summary" in md


def test_register_adds_tool():
    h = ProtocolHandler()
    gds.register(h)
    assert h.has("get_document_summary")