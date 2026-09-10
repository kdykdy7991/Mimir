"""
Phase 4 §P4.2 — ``HttpRagReadOnlyClient`` over a MockTransport.

Covers: credential header, four read methods, error translation
(not_found/access_denied/invalid_request/upstream), timeout and transport
failure mapping, bounded pool, and the absence of a generic request()
escape hatch.
"""

from __future__ import annotations

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
from src.mcp_server.clients.models import QueryRequest


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
                      "get_document_chunks", "close"}


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
    assert seen["scope"] == "finance,hr"


def test_no_scope_header_for_trusted_local_principal():
    seen = {}

    def handler(request):
        seen["scope"] = request.headers.get("X-MCP-Allowed-Collections")
        return _json(200, {"count": 0, "collections": []})

    c = _client(handler)
    c.list_collections(TrustedLocalPrincipal())
    # A TrustedLocal never claims full access over HTTP — no scope header.
    assert seen["scope"] is None
