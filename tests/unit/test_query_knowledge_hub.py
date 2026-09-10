"""
P2.2 — ``query_knowledge_hub`` normalised evidence contract.

Covers: validation (empty / too-long query), collection selection,
rerank precedence (canonical ``rerank`` over deprecated ``no_rerank``),
and evidence/diagnostics structured output with the compatibility
``n_results`` / ``citations`` mirrors.
"""

from __future__ import annotations

from src.mcp_server.clients.models import (
    Diagnostics,
    EvidenceItem,
    KnowledgeQueryResult,
)
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import query_knowledge_hub as qkh


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _evidenced_result(query="q", collection="kb"):
    return KnowledgeQueryResult(
        query=query, collection=collection, count=2,
        diagnostics=Diagnostics(degraded=True, reasons=["reranker down"], trace_id="tr"),
        evidence=[
            EvidenceItem(rank=1, chunk_id="c1", document_id="d1",
                         source="a.pdf", page=3, score=0.9,
                         text="evidence-one" + "x" * 250),
            EvidenceItem(rank=2, chunk_id="c2", document_id="d1",
                         source="b.md", score=0.5, text="short"),
        ],
    )


def _fake(result):
    class FakeClient:
        def __init__(self): self.req = None
        def query_knowledge(self, request, principal):
            self.req = request
            return KnowledgeQueryResult(
                query=request.query, collection=request.collection,
                count=len(result.evidence), evidence=result.evidence,
                diagnostics=result.diagnostics,
            )
    return FakeClient()


def test_empty_query_rejected():
    result = _run(qkh._query_knowledge_hub({"query": "  "}))
    assert result.is_error
    assert "query" in result.content[0].text


def test_oversized_query_rejected():
    result = _run(qkh._query_knowledge_hub({"query": "q" * (qkh.MAX_QUERY_LENGTH + 1)}))
    assert result.is_error
    assert "limit" in result.content[0].text


def test_no_answer_is_returned():
    """The tool only returns evidence — no answer string is ever produced."""
    fake = _fake(_evidenced_result())
    md, structured = _run(qkh._query_knowledge_hub({"query": "q", "_client": fake}))
    assert structured["count"] == 2
    assert "answer" not in structured
    assert isinstance(structured["evidence"], list)
    assert structured["evidence"][0]["rank"] == 1


def test_structured_has_evidence_and_diagnostics():
    fake = _fake(_evidenced_result())
    md, structured = _run(qkh._query_knowledge_hub({"query": "q", "_client": fake}))
    # Direct call → TrustedLocalPrincipal resolves to the legacy default.
    assert structured["collection"] == "default"
    assert structured["diagnostics"] == {
        "degraded": True, "reasons": ["reranker down"], "trace_id": "tr",
    }
    assert structured["evidence"][0]["chunk_id"] == "c1"
    assert structured["evidence"][0]["text"].startswith("evidence-one")


def test_compat_n_results_and_citations_present():
    fake = _fake(_evidenced_result())
    md, structured = _run(qkh._query_knowledge_hub({"query": "q", "_client": fake}))
    assert structured["n_results"] == 2
    assert len(structured["citations"]) == 2
    assert structured["citations"][0]["index"] == 1


def test_no_rerank_deprecated_alias_sets_rerank_false():
    fake = _fake(_evidenced_result())
    _run(qkh._query_knowledge_hub({"query": "q", "no_rerank": True, "_client": fake}))
    assert fake.req.rerank is False


def test_rerank_canonical_wins_over_no_rerank():
    fake = _fake(_evidenced_result())
    _run(qkh._query_knowledge_hub(
        {"query": "q", "no_rerank": True, "rerank": True, "_client": fake},
    ))
    assert fake.req.rerank is True  # canonical param wins


def test_rerank_defaults_true():
    fake = _fake(_evidenced_result())
    _run(qkh._query_knowledge_hub({"query": "q", "_client": fake}))
    assert fake.req.rerank is True
    assert fake.req.top_k == 10


def test_multi_collection_omitted_returns_tool_error():
    class Multi:
        key_id = "k"
        name = "n"
        allowed_collections = frozenset({"a", "b"})
    fake = _fake(_evidenced_result())
    h = ProtocolHandler()
    qkh.register(h)
    out = _run(h.dispatch(
        "query_knowledge_hub", {"query": "x", "_client": fake}, principal=Multi(),
    ))
    assert out.is_error
    assert "collection" in out.content[0].text


def test_register_adds_tool():
    h = ProtocolHandler()
    qkh.register(h)
    assert h.has("query_knowledge_hub")