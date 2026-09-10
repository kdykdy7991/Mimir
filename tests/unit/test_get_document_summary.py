"""
P2.3 — ``get_document_summary`` compatibility alias.

Shares the ``get_document`` implementation; only its input schema uses the
legacy ``doc_id`` param. Both routes return identical structured output.
"""

from __future__ import annotations

from src.mcp_server.clients.models import DocumentInfo
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import get_document_summary as alias


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _h():
    h = ProtocolHandler()
    alias.register(h)
    return h


def test_alias_requires_doc_id():
    schema = _h().get("get_document_summary").input_schema
    assert schema["required"] == ["doc_id"]
    assert "doc_id" in schema["properties"]


def test_alias_routes_doc_id_to_shared_handler():
    class FakeClient:
        def __init__(self): self.calls = []
        def get_document(self, document_id, principal):
            self.calls.append(document_id)
            return DocumentInfo(document_id=document_id, collection="kb",
                                title="T", source="/doc.pdf", chunk_count=1)
    fake = FakeClient()
    # The alias handler is the same _get_document_item; resolve via its id.
    from src.mcp_server.tools import get_document as gd
    md, structured = _run(gd._get_document_item({"doc_id": "u-id", "_client": fake}))
    assert fake.calls == ["u-id"]
    assert structured["doc_id"] == "u-id"
    assert structured["document_id"] == "u-id"
    assert structured["source_path"] == "/doc.pdf"


def test_alias_not_found_uses_unified_message():
    from src.mcp_server.clients.errors import ResourceNotFoundError
    from src.mcp_server.tools import get_document as gd
    class NF:
        def get_document(self, document_id, principal):
            raise ResourceNotFoundError("nope")
    result = _run(gd._get_document_item({"doc_id": "u-id", "_client": NF()}))
    assert result.is_error
    assert result.content[0].text == "document not found or not accessible"


def test_register_adds_summary_alias():
    assert _h().has("get_document_summary")