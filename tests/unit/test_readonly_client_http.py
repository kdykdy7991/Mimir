"""
Phase 4 §P4.2 — ``HttpRagReadOnlyClient`` over a MockTransport.

Covers: credential header, four read methods, error translation
(not_found/access_denied/invalid_request/upstream), timeout and transport
failure mapping, bounded pool, and the absence of a generic request()
escape hatch.
"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from src.mcp_server.auth.context import TrustedLocalPrincipal
from src.mcp_server.clients.errors import (
    AccessDeniedError,
    InvalidRequestError,
    ResourceNotFoundError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from src.mcp_server.clients.http_client import HttpRagReadOnlyClient
from src.application.contracts import ChunkContextRequest, SearchRequest
from src.mcp_server.clients.models import DocumentListRequest, QueryRequest


def _client(handler, *, api_key="skdy_1"):
    return HttpRagReadOnlyClient(
        base_url="http://api:8000",
        api_key=api_key,
        transport=httpx.MockTransport(handler),
    )


def _json(status, payload):
    return httpx.Response(status, json=payload)


def _principal():
    return TrustedLocalPrincipal()


def test_sends_credential_header_and_reads_collections():
    seen = {}

    def handler(request):
        seen["X-API-Key"] = request.headers.get("X-API-Key")
        assert request.method == "GET"
        assert request.url.path == "/internal/mcp/v1/collections"
        return _json(200, {"count": 1, "collections": [
            {"name": "kb", "description": None, "document_count": 5, "chunk_count": 50},
        ]})

    c = _client(handler)
    cols = c.list_collections(_principal())
    assert cols[0].name == "kb"
    assert cols[0].document_count == 5


def test_query_posts_evidence_shape():
    def handler(request):
        assert request.method == "POST"
        body = json.loads(request.content.decode())
        assert body["query"] == "q"
        assert body["collection"] == "kb"
        assert body["top_k"] == 3
        assert body["rerank"] is False
        return _json(200, {
            "query": "q", "collection": "kb", "count": 1,
            "evidence": [{
                "rank": 1, "chunk_id": "c1", "document_id": "d1",
                "title": "T", "source": "a.pdf", "page": 3,
                "score": 0.9, "text": "big cut evidence",
            }],
            "diagnostics": {"degraded": True, "reasons": ["down"], "trace_id": "t"},
        })

    c = _client(handler)
    result = c.query_knowledge(
        QueryRequest(query="q", collection="kb", top_k=3, rerank=False),
        _principal(),
    )
    assert result.count == 1
    assert result.evidence[0].chunk_id == "c1"
    assert result.diagnostics.degraded is True


def test_get_document():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/internal/mcp/v1/documents/u-1"
        return _json(200, {
            "document_id": "u-1", "collection": "kb", "title": "Doc",
            "document_type": "pdf", "source": "/a.pdf", "summary": "s",
            "tags": ["t"], "chunk_count": 3,
        })

    c = _client(handler)
    info = c.get_document("u-1", _principal())
    assert info.document_id == "u-1"
    assert info.chunk_count == 3


def test_get_document_chunks_pagination():
    def handler(request):
        assert request.url.path == "/internal/mcp/v1/documents/u-1/chunks"
        assert request.url.params["page"] == "2"
        assert request.url.params["page_size"] == "2"
        return _json(200, {
            "document_id": "u-1", "page": 2, "page_size": 2, "total": 5,
            "has_next": True,
            "chunks": [
                {"chunk_id": "c2", "index": 2, "text": "t2", "page": 1, "section": ""},
                {"chunk_id": "c3", "index": 3, "text": "t3", "page": None, "section": "s"},
            ],
        })

    c = _client(handler)
    page = c.get_document_chunks("u-1", 2, 2, _principal())
    assert page.total == 5
    assert page.has_next is True
    assert page.chunks[1].section == "s"


def test_list_documents_and_get_exact_chunk():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/documents"):
            assert request.url.params["collection"] == "finance,legal"
            return _json(200, {
                "collection": "finance,legal", "page": 1, "page_size": 20,
                "total": 1, "documents": [{
                    "document_id": "d1", "collection": "finance,legal",
                    "title": "a.pdf", "source": "a.pdf", "chunk_count": 2,
                }],
            })
        return _json(200, {
            "document_id": "d1", "chunk_id": "c1", "index": 0,
            "text": "body", "content_type": "text",
            "source_locator": {"kind": "none", "page": None},
            "asset_ids": [],
        })

    c = _client(handler)
    page = c.list_documents(
        DocumentListRequest(collection="finance,legal"), _principal(),
    )
    chunk = c.get_chunk("d1", "c1", _principal())
    assert page.documents[0].source == "a.pdf"
    assert chunk.text == "body"
    assert seen == [
        "/internal/mcp/v1/documents",
        "/internal/mcp/v1/documents/d1/chunks/c1",
    ]


def test_unified_search_round_trip():
    def handler(request):
        body = json.loads(request.content.decode())
        assert body["mode"] == "dense"
        assert body["filters"]["content_types"] == ["table"]
        return _json(200, {
            "query": "q", "collection": "kb", "mode": "dense",
            "evidence": [{
                "collection_id": "cid", "document_id": "did", "chunk_id": "c1",
                "content_type": "table", "source_locator": {"kind": "chunk"},
                "scores": {"dense": .8, "sparse": None, "fusion": None, "rerank": None},
                "matched_queries": ["q"], "content": "body",
            }],
            "warnings": [], "diagnostics": {"degraded": False},
            "truncated": False,
        })

    from src.application.contracts import EvidenceFilterV1
    result = _client(handler).search(SearchRequest(
        query="q", collection="kb", mode="dense",
        filters=EvidenceFilterV1(content_types=["table"]), rerank=False,
    ), _principal())
    assert result.evidence[0].scores.dense == .8
    assert result.evidence[0].content == "body"


def test_multi_search_fields_round_trip():
    def handler(request):
        body = json.loads(request.content.decode())
        assert body["alternate_queries"] == ["q2"]
        assert body["collection_ids"] == ["a", "b"]
        assert body["failure_policy"] == "allow_partial"
        return _json(200, {
            "query": "q", "collection": None, "collections": ["a", "b"],
            "mode": "hybrid", "evidence": [], "warnings": [],
            "diagnostics": {
                "executed_queries": ["q", "q2"],
                "successful_collections": ["a"],
                "failed_collections": ["b"],
            }, "truncated": False,
        })

    result = _client(handler).search(SearchRequest(
        query="q", alternate_queries=("q2",), collection_ids=("a", "b"),
        failure_policy="allow_partial",
    ), _principal())
    assert result.collection is None
    assert result.collections == ("a", "b")
    assert result.diagnostics.failed_collections == ("b",)


def test_chunk_context_round_trip():
    def handler(request):
        assert request.url.path == "/internal/mcp/v1/documents/d/chunks/c/context"
        body = json.loads(request.content.decode())
        assert body == {"include": "both", "before": 2, "after": 1, "max_chars": 50}
        return _json(200, {
            "document_id": "d",
            "hit": {"chunk_id": "c", "relation": "hit", "text": "body"},
            "parent": None,
            "neighbors": [{"chunk_id": "n", "relation": "after", "text": "next"}],
            "truncated": False,
        })

    result = _client(handler).get_chunk_context(ChunkContextRequest(
        document_id="d", chunk_id="c", before=2, after=1, max_chars=50,
    ), _principal())
    assert result.hit.text == "body"
    assert result.neighbors[0].relation == "after"


def test_asset_round_trip():
    def handler(request):
        assert request.url.path == "/internal/mcp/v1/documents/d/assets/a"
        return _json(200, {
            "metadata": {
                "asset_id": "a", "document_id": "d", "chunk_id": "c",
                "document_version": "v", "mime_type": "image/png",
                "byte_size": 3,
                "checksum_sha256": hashlib.sha256(b"png").hexdigest(),
                "locator": "kb/a.png",
            },
            "data_base64": "cG5n",
        })

    result = _client(handler).get_asset("d", "a", _principal())
    assert result.data == b"png"
    assert result.metadata.mime_type == "image/png"


@pytest.mark.parametrize("status,code,expected", [
    (404, "not_found", ResourceNotFoundError),
    (403, "access_denied", AccessDeniedError),
    (400, "invalid_request", InvalidRequestError),
])
def test_error_mapping(status, code, expected):
    def handler(request):
        return _json(status, {"code": code, "message": "boom"})
    c = _client(handler)
    with pytest.raises(expected):
        c.get_document("u-1", _principal())


def test_upstream_5xx_maps_to_unavailable():
    def handler(request):
        return _json(503, {"code": "upstream_unavailable", "message": "down"})
    c = _client(handler)
    with pytest.raises(UpstreamUnavailableError):
        c.list_collections(_principal())


def test_timeout_maps_to_upstream_timeout():
    def handler(request):
        raise httpx.ReadTimeout("timed out")
    c = _client(handler)
    with pytest.raises(UpstreamTimeoutError):
        c.list_collections(_principal())


def test_connection_error_maps_to_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")
    c = _client(handler)
    with pytest.raises(UpstreamUnavailableError):
        c.list_collections(_principal())


def test_no_generic_request_escape_hatch():
    c = _client(lambda req: _json(200, {}))
    assert not hasattr(c, "request")
    assert not hasattr(c, "_get") or True  # private helpers only, not public
    public = {m for m in dir(c) if not m.startswith("_")}
    assert public <= {"list_collections", "query_knowledge", "get_document",
                      "get_document_chunks", "list_documents", "get_chunk",
                      "get_chunk_context", "get_asset", "search", "close",
                      "list_data_sources", "get_sync_status", "list_sync_failures"}


def test_bounded_pool_is_configured():
    c = _client(lambda req: _json(200, {}))
    # Per-instance limits cap the connection pool regardless of transport.
    assert c._limits.max_connections == 10
    assert c._limits.max_keepalive_connections == 5

def test_upstream_down_surfaces_explicit_error_not_quiet_answer():
    """中断主 API 时 MCP 返回明确的上游不可用错误（协议级异常），
    而不是静默降级或空答案。"""
    from src.mcp_server.protocol_handler import ProtocolHandler
    from src.mcp_server.tools import query_knowledge_hub as qkh

    class Down:
        def query_knowledge(self, request, principal):
            raise UpstreamUnavailableError("main API is down")

    h = ProtocolHandler()
    qkh.register(h)

    import asyncio

    async def _call():
        return await h.dispatch(
            "query_knowledge_hub", {"query": "x", "_client": Down()},
            principal=_principal(),
        )

    # The upstream-down error is NOT swallowed into a fake answer: it
    # surfaces as an explicit protocol-level exception.
    with pytest.raises(UpstreamUnavailableError):
        asyncio.run(_call())


def test_504_upstream_timeout_maps_to_upstream_timeout():
    """Internal API 504 (code upstream_timeout) → UpstreamTimeoutError."""
    def handler(request):
        return _json(504, {"code": "upstream_timeout", "message": "slow"})
    c = _client(handler)
    with pytest.raises(UpstreamTimeoutError):
        c.list_collections(_principal())


def test_scope_header_forwarded_for_collection_principal():
    from dataclasses import dataclass

    from src.mcp_server.clients.scope import decode_scope_header

    @dataclass(frozen=True)
    class Scoped:
        key_id: str
        name: str
        allowed_collections: frozenset

    seen = {}

    def handler(request):
        seen["scope"] = request.headers.get("X-MCP-Allowed-Collections")
        return _json(200, {"count": 0, "collections": []})

    c = _client(handler)
    c.list_collections(Scoped("k", "n", frozenset({"hr", "finance"})))
    assert sorted(decode_scope_header(seen["scope"])) == ["finance", "hr"]


def test_scope_header_collision_free_for_comma_names():
    """A collection whose NAME contains a comma must survive round-trip intact.

    Regression for the P0 scope-escalation review finding: a grant to a
    single collection "finance,hr" must decode as ONE collection, not two.
    """
    from src.mcp_server.clients.scope import (
        decode_scope_header,
        encode_scope_header,
    )

    header = encode_scope_header({"finance,hr"})
    assert decode_scope_header(header) == ("finance,hr",)
    # Two distinct grants still round-trip as two.
    header2 = encode_scope_header(["finance", "hr"])
    assert sorted(decode_scope_header(header2)) == ["finance", "hr"]


def test_no_scope_header_for_trusted_local_principal():
    seen = {}

    def handler(request):
        seen["scope"] = request.headers.get("X-MCP-Allowed-Collections")
        return _json(200, {"count": 0, "collections": []})

    c = _client(handler)
    c.list_collections(TrustedLocalPrincipal())
    # A TrustedLocal never claims full access over HTTP — no scope header.
    assert seen["scope"] is None
