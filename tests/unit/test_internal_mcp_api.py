"""
Phase 4 §P4.1 — internal read-only MCP API.

The router reaches storage only through a RagReadOnlyClient. We inject a
fake client so the tests exercise routing, payload shaping, stable error
codes (no stack leak) and the read-only surface without touching real
Chroma / BM25.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.mcp_server.clients.errors import (
    AccessDeniedError,
    InvalidRequestError,
    ResourceNotFoundError,
    UpstreamUnavailableError,
)
from src.mcp_server.clients.models import (
    Diagnostics,
    DocumentChunk,
    DocumentChunkPage,
    DocumentInfo,
    EvidenceItem,
    KnowledgeQueryResult,
)
from src.web_api.app import create_app


def _make_client():
    class FakeClient:
        def list_collections(self, principal):
            return []
        def query_knowledge(self, request, principal):
            return KnowledgeQueryResult(
                query=request.query, collection=request.collection, count=1,
                diagnostics=Diagnostics(degraded=False, reasons=[], trace_id=None),
                evidence=[
                    EvidenceItem(rank=1, chunk_id="c1", document_id="d1",
                                 source="a.pdf", page=1, score=0.9, text="evidence"),
                ],
            )
        def get_document(self, document_id, principal):
            return DocumentInfo(document_id=document_id, collection="kb", title="T",
                                source="/a.pdf", tags=["t"], chunk_count=3)
        def get_document_chunks(self, document_id, page, page_size, principal):
            return DocumentChunkPage(document_id=document_id, page=page, page_size=page_size,
                                     total=4, chunks=[DocumentChunk(chunk_id="c1", index=0,
                                                                    text="chunk0", page=1)])
    return FakeClient()


@pytest.fixture()
def client(monkeypatch):
    import src.web_api.internal_mcp as im
    monkeypatch.setattr(im, "_client", lambda request: _make_client())
    app = create_app()
    return TestClient(app)


def test_collections_read_only_surface(client):
    r = client.get("/internal/mcp/v1/collections")
    assert r.status_code == 200
    body = r.json()
    assert "count" in body and "collections" in body


def test_query_endpoint_shapes_evidence(client):
    r = client.post("/internal/mcp/v1/query", json={
        "query": "q", "collection": "kb", "top_k": 5, "rerank": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["evidence"][0]["chunk_id"] == "c1"
    assert body["diagnostics"]["degraded"] is False


def test_document_and_chunks_endpoints(client):
    r = client.get("/internal/mcp/v1/documents/d1")
    assert r.status_code == 200
    assert r.json()["document_id"] == "d1"
    r2 = client.get("/internal/mcp/v1/documents/d1/chunks?page=1&page_size=2")
    assert r2.status_code == 200
    assert r2.json()["chunks"][0]["chunk_id"] == "c1"


def test_no_mutation_verbs_on_read_surface(client):
    # All internal endpoints are GET/POST only — reject writes.
    assert client.post("/internal/mcp/v1/collections").status_code == 405
    assert client.delete("/internal/mcp/v1/documents/x").status_code == 405
    assert client.put("/internal/mcp/v1/documents/x/chunks?page=1").status_code == 405


@pytest.mark.parametrize("exc,status,code", [
    (ResourceNotFoundError("gone"), 404, "not_found"),
    (AccessDeniedError("denied"), 403, "access_denied"),
    (InvalidRequestError("bad"), 400, "invalid_request"),
    (UpstreamUnavailableError("down"), 503, "upstream_unavailable"),
])
def test_stable_error_codes(monkeypatch, exc, status, code):
    import src.web_api.internal_mcp as im
    class FakeErr:
        def get_document(self, document_id, principal):
            raise exc
    monkeypatch.setattr(im, "_client", lambda request: FakeErr())
    app = create_app()
    r = TestClient(app).get("/internal/mcp/v1/documents/x")
    assert r.status_code == status
    body = r.json()
    assert body["code"] == code
    assert "Traceback" not in r.text


def test_openapi_marks_internal_readonly(client):
    schema = client.get("/openapi.json").json()
    internal_paths = [p for p in schema["paths"] if p.startswith("/internal")]
    assert len(internal_paths) == 4
    # Only read verbs.
    for p in internal_paths:
        for method in schema["paths"][p]:
            assert method in ("get", "post", "parameters")
    assert schema["paths"]["/internal/mcp/v1/collections"]["get"]["tags"] == [
        "internal-mcp-readonly",
    ]