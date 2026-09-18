"""
P1.2b — ``InProcessRagReadOnlyClient.query_knowledge`` evidence mapping.

Retrieval is exercised through a fake query service (injected by patching
``_build_search``) so the test needs no embedding / vector store. Verifies
the raw-evidence contract: no answer, no LLM call, Diagnostic fields, and
legacy ``n_results``/``citations`` mirrors.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.mcp_server.auth.context import TrustedLocalPrincipal
from src.mcp_server.auth.models import AccessPrincipal
from src.mcp_server.clients.errors import AccessDeniedError, UpstreamUnavailableError
import pytest
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
from src.mcp_server.clients.models import QueryRequest
from src.application.contracts import SearchRequest
from src.core.types import ChunkRecord, RetrievalResult


def _chunk(**meta):
    return RetrievalResult(
        chunk=ChunkRecord(id="doc_0001_hash", text="body text", metadata=meta),
        score=0.91,
        source="fusion",
    )


def _client(service):
    c = InProcessRagReadOnlyClient(data_dir="/tmp/unused", services=SimpleNamespace())
    c._build_search = lambda **kw: (service, None)
    c._record_mcp_query = lambda **kw: None
    return c


def test_query_maps_evidence_without_answer():
    search = SimpleNamespace(
        chunks=[
            _chunk(source_path="/docs/refund.pdf", title="售后政策",
                   page_num=3, chunk_index=0),
        ],
        degraded=False,
        degraded_reasons=[],
        trace_id="tr-1",
    )
    service = SimpleNamespace(search=lambda query, **kw: search)
    client = _client(service)
    result = client.query_knowledge(
        QueryRequest(query="退款规则", collection="kb", top_k=8, rerank=False),
        TrustedLocalPrincipal(),
    )
    assert result.count == 1
    ev = result.evidence[0]
    assert ev.rank == 1
    assert ev.chunk_id == "doc_0001_hash"
    assert ev.title == "售后政策"
    assert ev.source == "refund.pdf"
    assert ev.page == 3
    assert ev.score == 0.91
    assert ev.source_type == "fusion"
    assert ev.document_id  # stable derived id present
    # Canonical + legacy mirror.
    assert result.n_results == result.count == 1
    assert result.citations[0]["chunk_id"] == "doc_0001_hash"
    # No answer/conclusion fields are ever produced.
    assert "answer" not in result.__dict__
    assert not hasattr(result, "answer")


def test_query_reports_degraded_and_reasons():
    search = SimpleNamespace(
        chunks=[_chunk(source_path="/x.md")],
        degraded=True,
        degraded_reasons=["dense retrieval failed; falling back"],
        trace_id="tr-2",
    )
    service = SimpleNamespace(search=lambda query, **kw: search)
    client = _client(service)
    result = client.query_knowledge(
        QueryRequest(query="q", collection="kb", rerank=False),
        TrustedLocalPrincipal(),
    )
    assert result.diagnostics.degraded is True
    assert result.diagnostics.reasons == [
        "dense retrieval failed; falling back",
    ]
    assert result.diagnostics.trace_id == "tr-2"


def test_query_empty_result_returns_empty_evidence():
    search = SimpleNamespace(
        chunks=[], degraded=False, degraded_reasons=[], trace_id=None,
    )
    service = SimpleNamespace(search=lambda query, **kw: search)
    client = _client(service)
    result = client.query_knowledge(
        QueryRequest(query="q", collection="kb", rerank=False),
        TrustedLocalPrincipal(),
    )
    assert result.count == 0
    assert result.evidence == []


def test_query_rerank_unavailable_degrades():
    """Reranker unavailable must set degraded, not silently succeed."""
    search = SimpleNamespace(
        chunks=[_chunk(source_path="/y.md")],
        degraded=False, degraded_reasons=[], trace_id=None,
    )
    service = SimpleNamespace(search=lambda query, **kw: search)
    client = InProcessRagReadOnlyClient(data_dir="/tmp/unused", services=SimpleNamespace())
    client._build_search = lambda **kw: (service, None)  # no rerank stage
    client._record_mcp_query = lambda **kw: None
    result = client.query_knowledge(
        QueryRequest(query="q", collection="kb", rerank=True),
        TrustedLocalPrincipal(),
    )
    assert result.diagnostics.degraded is True
    assert any("reranker" in r for r in result.diagnostics.reasons)
    assert result.count == 1  # evidence still returned


def test_multi_collection_search_is_fair_and_allows_explicit_partial_failure():
    client = InProcessRagReadOnlyClient(
        data_dir="/tmp/unused", services=SimpleNamespace(),
    )

    def build(*, collection, rerank):
        if collection == "broken":
            raise RuntimeError("offline")
        hit = RetrievalResult(
            chunk=ChunkRecord(
                id=f"{collection}-chunk", text=collection,
                metadata={"source_path": f"/{collection}.md"},
            ), score=.8, source="fusion",
        )
        result = SimpleNamespace(
            chunks=[hit], degraded=False, degraded_reasons=[], trace_id=None,
            dense_count=0, sparse_count=0, fused_count=1,
        )
        return SimpleNamespace(search=lambda query, **kw: result), None

    client._build_search = build
    result = client.search(SearchRequest(
        query="q", collection_ids=("a", "broken", "b"), rerank=False,
        failure_policy="allow_partial",
    ), TrustedLocalPrincipal())

    assert result.collections == ("a", "broken", "b")
    assert [item.chunk_id for item in result.evidence] == ["a-chunk", "b-chunk"]
    assert result.diagnostics.successful_collections == ("a", "b")
    assert result.diagnostics.failed_collections == ("broken",)
    assert len(result.warnings) == 1


def test_multi_collection_authorization_rejects_whole_request_before_retrieval():
    client = InProcessRagReadOnlyClient(
        data_dir="/tmp/unused", services=SimpleNamespace(),
    )
    client._build_search = lambda **kw: pytest.fail("retrieval must not start")
    principal = AccessPrincipal(
        key_id="k", name="test", allowed_collections=frozenset({"a"}),
    )
    with pytest.raises(AccessDeniedError):
        client.search(SearchRequest(
            query="q", collection_ids=("a", "not-authorized"), rerank=False,
        ), principal)


def test_allow_partial_still_errors_when_every_collection_fails():
    client = InProcessRagReadOnlyClient(
        data_dir="/tmp/unused", services=SimpleNamespace(),
    )
    client._build_search = lambda **kw: (_ for _ in ()).throw(RuntimeError("down"))
    with pytest.raises(UpstreamUnavailableError, match="every requested"):
        client.search(SearchRequest(
            query="q", collection_ids=("a", "b"), rerank=False,
            failure_policy="allow_partial",
        ), TrustedLocalPrincipal())
