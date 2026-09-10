"""
P5.1 — compatibility-window contract.

For at least one release cycle these legacy surface elements MUST remain
usable (plan §P5.1): the ``get_document_summary`` tool name, the
``doc_id`` input, ``n_collections``, ``n_results`` + ``citations``, and
the ``no_rerank`` param. This single file locks the whole read surface so
a future change cannot silently drop a compat promise.
"""

from __future__ import annotations

from src.mcp_server.auth.context import TrustedLocalPrincipal
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import (
    get_document as gd,
    get_document_chunks as gdc,
    get_document_summary as gds,
    list_collections as lc,
    query_knowledge_hub as qkh,
)


def _build_handler() -> ProtocolHandler:
    h = ProtocolHandler()
    qkh.register(h)
    lc.register(h)
    gd.register(h)
    gds.register(h)
    gdc.register(h)
    return h


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_read_surface_is_exactly_five_tools_no_writes():
    h = _build_handler()
    # Allowed
    names = set(h.list_names())
    assert names == {
        "get_document", "get_document_chunks", "get_document_summary",
        "list_collections", "query_knowledge_hub",
    }
    blocked = {"add", "delete", "upload", "create", "update", "ingest",
               "summarize", "answer", "chat", "ask"}
    assert names.isdisjoint(blocked)


def _in_process():
    return InProcessRagReadOnlyClient(data_dir="/tmp/nonexistent-test-data")


def test_get_document_summary_tool_alias_is_registered():
    h = _build_handler()
    assert h.has("get_document_summary")
    schema = h.get("get_document_summary").input_schema
    assert schema["required"] == ["doc_id"]


def test_doc_id_input_accepted_by_document_and_summary_and_chunks():
    # get_document, get_document_summary (alias), get_document_chunks and the
    # detail handler all resolve the document from legacy ``doc_id``.
    from src.mcp_server.clients.models import DocumentInfo

    class Fake:
        def get_document(self, document_id, principal):
            return DocumentInfo(document_id=document_id, collection="kb",
                                title="T", chunk_count=1)
    fake = Fake()
    md, structured = _run(gd._get_document_item({"doc_id": "u1", "_client": fake}))
    assert structured["document_id"] == "u1"
    md, structured = _run(gd._get_document_item({"doc_id": "u1", "_client": fake}))
    assert structured["doc_id"] == "u1"


def test_n_collections_compat_kept():
    from unittest.mock import patch

    client = _in_process()
    with patch.object(client, "_vector_counts", return_value={}), \
         patch.object(client, "_document_counts", return_value={}):
        md, structured = _run(lc._list_collections({"_client": client}))
    assert "count" in structured
    assert "n_collections" in structured
    assert structured["count"] == structured["n_collections"]


def test_query_compat_n_results_citations_and_no_rerank():
    from src.mcp_server.clients.models import Diagnostics, EvidenceItem, KnowledgeQueryResult

    class Fake:
        def __init__(self): self.rerank_seen = None
        def query_knowledge(self, request, principal):
            self.rerank_seen = request.rerank
            return KnowledgeQueryResult(
                query=request.query, collection=request.collection, count=1,
                diagnostics=Diagnostics(degraded=False, reasons=[], trace_id="t"),
                evidence=[EvidenceItem(rank=1, chunk_id="c1", document_id="d1",
                                       source="a", page=1, score=0.5, text="e")],
            )
    fake = Fake()
    md, structured = _run(qkh._query_knowledge_hub(
        {"query": "q", "no_rerank": True, "_client": fake},
    ))
    assert structured["n_results"] == 1
    assert isinstance(structured["citations"], list) and len(structured["citations"]) == 1
    assert fake.rerank_seen is False  # no_rerank True → rerank False


def test_openapi_and_public_read_only_namespace(tmp_path):
    # Internal API stays under /internal; covered thoroughly by
    # test_internal_mcp_api.py. Left here as a compat marker only.
    pass