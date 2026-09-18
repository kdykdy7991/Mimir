"""
Phase 4 §P4.1 — internal read-only MCP API (review-fixed).

Covers: authentication (missing/wrong key → 401, unconfigured → 503),
collection-scope propagation to the client (isolation), stable error
codes (no stack leak), read-only surface, and OpenAPI markings.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

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
    ChunkDetail,
    DocumentChunk,
    DocumentChunkPage,
    DocumentInfo,
    DocumentPage,
    DocumentSummary,
    EvidenceItem,
    KnowledgeQueryResult,
)
from src.web_api.app import create_app
from src.application.contracts import (
    AssetContent,
    AssetV1,
    ChunkContextResult,
    ContextChunkV1,
    SearchDiagnostics,
    SearchResult,
    RevisionDiff,
    RevisionSource,
    RevisionStatus,
    RevisionSummary,
)

KEY = "test-internal-key"


class _RecordingClient:
    """Fakes the in-process client and records the effective principal scope."""

    def __init__(self):
        self.scopes = []
        self.search_requests = []

    def list_collections(self, principal):
        self.scopes.append(("list", getattr(principal, "allowed_collections", None)))
        return []

    def query_knowledge(self, request, principal):
        self.scopes.append(("query", getattr(principal, "allowed_collections", None)))
        return KnowledgeQueryResult(
            query=request.query, collection=request.collection, count=1,
            diagnostics=Diagnostics(degraded=False, reasons=[], trace_id=None),
            evidence=[EvidenceItem(rank=1, chunk_id="c1", document_id="d1",
                                   source="a.pdf", page=1, score=0.9, text="e")],
        )

    def get_document(self, document_id, principal):
        self.scopes.append(("doc", getattr(principal, "allowed_collections", None)))
        return DocumentInfo(document_id=document_id, collection="kb", title="T",
                            source="/a.pdf", tags=["t"], chunk_count=3)

    def get_document_chunks(self, document_id, page, page_size, principal):
        self.scopes.append(("chunks", getattr(principal, "allowed_collections", None)))
        return DocumentChunkPage(document_id=document_id, page=page, page_size=page_size,
                                 total=1, chunks=[DocumentChunk(chunk_id="c1",
                                                                index=0, text="t")])

    def list_documents(self, request, principal):
        self.scopes.append(("documents", getattr(principal, "allowed_collections", None)))
        return DocumentPage(
            collection=request.collection, page=request.page,
            page_size=request.page_size, total=1,
            documents=[DocumentSummary(
                document_id="d1", collection=request.collection,
                title="a.pdf", source="a.pdf",
            )],
        )

    def get_chunk(self, document_id, chunk_id, principal):
        self.scopes.append(("chunk", getattr(principal, "allowed_collections", None)))
        return ChunkDetail(
            document_id=document_id, chunk_id=chunk_id, index=0, text="body",
            source_locator={"kind": "none", "page": None},
        )

    def get_chunk_context(self, request, principal):
        self.scopes.append(("context", getattr(principal, "allowed_collections", None)))
        return ChunkContextResult(
            document_id=request.document_id,
            hit=ContextChunkV1(
                chunk_id=request.chunk_id, relation="hit", text="body",
            ),
        )

    def get_asset(self, document_id, asset_id, principal):
        self.scopes.append(("asset", getattr(principal, "allowed_collections", None)))
        return AssetContent(
            metadata=AssetV1(
                asset_id=asset_id, document_id=document_id, chunk_id="c",
                document_version="v", mime_type="image/png", byte_size=3,
                checksum_sha256=hashlib.sha256(b"png").hexdigest(),
                locator="kb/a.png",
            ), data=b"png",
        )

    def search(self, request, principal):
        self.scopes.append(("search", getattr(principal, "allowed_collections", None)))
        self.search_requests.append(request)
        return SearchResult(
            query=request.query, collection=request.collection,
            collections=request.collections, mode=request.mode,
            diagnostics=SearchDiagnostics(trace_id="search-trace"),
        )


@pytest.fixture()
def client(monkeypatch):
    import src.web_api.internal_mcp as im
    rec = _RecordingClient()
    monkeypatch.setattr(im, "_internal_key", lambda: KEY)
    monkeypatch.setattr(im, "_client", lambda request: rec)
    app = create_app()
    return TestClient(app), rec


def _req(client, method, path, *, scope=None, key=KEY, **kw):
    headers = {"X-API-Key": key}
    if scope is not None:
        from src.mcp_server.clients.scope import encode_scope_header
        headers["X-MCP-Allowed-Collections"] = encode_scope_header(scope)
    if kw.get("headers"):
        headers.update(kw.pop("headers"))
    return client.request(method, path, headers=headers, **kw)


def test_requires_valid_key(client):
    tc, _ = client
    # missing key → 401
    r = tc.get("/internal/mcp/v1/collections")
    assert r.status_code == 401
    assert r.json()["code"] == "internal_unauthorized"
    # wrong key → 401
    r = _req(tc, "get", "/internal/mcp/v1/collections", key="nope")
    assert r.status_code == 401


def test_unconfigured_key_fails_closed():
    import src.web_api.internal_mcp as im_api

    tc = TestClient(create_app())
    with pytest.MonkeyPatch.context() as m:
        m.setattr(im_api, "_internal_key", lambda: "")
        r = tc.get("/internal/mcp/v1/collections", headers={"X-API-Key": "anything"})
    assert r.status_code == 503
    assert r.json()["code"] == "internal_api_not_configured"


def test_collections_read_only_surface(client):
    tc, rec = client
    r = _req(tc, "get", "/internal/mcp/v1/collections", scope=["finance"])
    assert r.status_code == 200
    assert "count" in r.json() and "collections" in r.json()
    assert rec.scopes[0] == ("list", frozenset({"finance"}))


def test_scope_is_deny_all_without_scope_header(client):
    tc, rec = client
    r = _req(tc, "get", "/internal/mcp/v1/collections")  # no scope header
    assert r.status_code == 200
    # The code path hands a deny-all principal (empty grants) to list — with a
    # real in-process client that returns no collections; here rec records it.
    assert rec.scopes[0][1] == frozenset()


def test_scope_propagates_to_query_and_unchanged_across_verbs(client):
    tc, rec = client
    _req(tc, "post", "/internal/mcp/v1/query",
         scope=["finance", "hr"],
         json={"query": "q", "collection": "finance", "top_k": 3, "rerank": False})
    assert rec.scopes[0] == ("query", frozenset({"finance", "hr"}))
    _req(tc, "get", "/internal/mcp/v1/documents/d1", scope=["hr"])
    assert rec.scopes[1] == ("doc", frozenset({"hr"}))
    _req(tc, "get", "/internal/mcp/v1/documents/d1/chunks?page=1&page_size=2",
         scope=["hr"])
    assert rec.scopes[2][0] == "chunks"


def test_query_endpoint_shapes_evidence(client):
    tc, rec = client
    r = _req(tc, "post", "/internal/mcp/v1/query",
             scope=["finance"], json={"query": "q", "collection": "finance"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["evidence"][0]["chunk_id"] == "c1"


def test_document_and_chunks_endpoints(client):
    tc, rec = client
    r = _req(tc, "get", "/internal/mcp/v1/documents/d1", scope=["finance"])
    assert r.status_code == 200 and r.json()["document_id"] == "d1"
    r2 = _req(tc, "get", "/internal/mcp/v1/documents/d1/chunks?page=1&page_size=2",
              scope=["finance"])
    assert r2.status_code == 200 and r2.json()["chunks"][0]["chunk_id"] == "c1"


def test_document_discovery_and_exact_chunk_endpoints(client):
    tc, rec = client
    listed = _req(
        tc, "get", "/internal/mcp/v1/documents",
        scope=["finance,legal"],
        params={"collection": "finance,legal", "page": 1, "page_size": 20},
    )
    assert listed.status_code == 200
    assert listed.json()["documents"][0]["source"] == "a.pdf"
    exact = _req(
        tc, "get", "/internal/mcp/v1/documents/d1/chunks/c1",
        scope=["finance,legal"],
    )
    assert exact.status_code == 200 and exact.json()["text"] == "body"
    assert rec.scopes[-2:] == [
        ("documents", frozenset({"finance,legal"})),
        ("chunk", frozenset({"finance,legal"})),
    ]


def test_unified_search_endpoint_is_scoped(client):
    tc, rec = client
    response = _req(
        tc, "post", "/internal/mcp/v1/search", scope=["finance"],
        json={"query": "q", "collection": "finance", "mode": "sparse",
              "filters": {"file_types": ["pdf"]}, "rerank": False},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "sparse"
    assert response.json()["diagnostics"]["trace_id"] == "search-trace"
    assert rec.scopes[-1] == ("search", frozenset({"finance"}))


def test_unified_search_endpoint_preserves_multi_search_contract(client):
    tc, rec = client
    response = _req(
        tc, "post", "/internal/mcp/v1/search", scope=["finance", "legal"],
        json={
            "query": "q", "alternate_queries": ["q2"],
            "collection_ids": ["finance", "legal"],
            "failure_policy": "allow_partial", "rerank": False,
        },
    )
    assert response.status_code == 200
    request = rec.search_requests[-1]
    assert request.queries == ("q", "q2")
    assert request.collections == ("finance", "legal")
    assert request.failure_policy.value == "allow_partial"
    assert response.json()["collections"] == ["finance", "legal"]


def test_chunk_context_endpoint_is_scoped_and_bounded(client):
    tc, rec = client
    response = _req(
        tc, "post", "/internal/mcp/v1/documents/d/chunks/c/context",
        scope=["finance"], json={"include": "neighbors", "max_chars": 99},
    )
    assert response.status_code == 200
    assert response.json()["hit"]["chunk_id"] == "c"
    assert rec.scopes[-1] == ("context", frozenset({"finance"}))


def test_asset_endpoint_is_scoped_and_base64_encoded(client):
    tc, rec = client
    response = _req(
        tc, "get", "/internal/mcp/v1/documents/d/assets/a", scope=["finance"],
    )
    assert response.status_code == 200
    assert response.json()["data_base64"] == "cG5n"
    assert "file_path" not in response.text
    assert rec.scopes[-1] == ("asset", frozenset({"finance"}))


def test_revision_history_detail_and_diff_endpoints_are_scoped(client, monkeypatch):
    import src.web_api.internal_mcp as im

    class FakeRevisionService:
        def __init__(self):
            self.scopes = []

        def list_history(self, document_id, chunk_id, principal):
            self.scopes.append(principal.allowed_collections)
            return (RevisionSummary(
                revision_id="r1", base_revision_id=None,
                source=RevisionSource.INITIAL, status=RevisionStatus.ACTIVE,
                reason="initial", actor="system",
                created_at=datetime.now(timezone.utc),
                checksum_sha256="a" * 64, is_current=True,
            ),)

        def get_revision(self, document_id, chunk_id, revision_id, principal):
            from src.application.contracts import ChunkRevision
            self.scopes.append(principal.allowed_collections)
            return ChunkRevision(
                revision_id="r1", collection="finance", document_id=document_id,
                chunk_id=chunk_id, text="old", source=RevisionSource.INITIAL,
                reason="initial", actor="system",
            ), RevisionStatus.ACTIVE, True

        def diff(self, document_id, chunk_id, before, after, principal):
            self.scopes.append(principal.allowed_collections)
            return RevisionDiff(before, after, "-old\n+new\n", False)

    service = FakeRevisionService()
    monkeypatch.setattr(im, "_revision_service", lambda request: service)
    tc, _ = client
    base = "/internal/mcp/v1/documents/d/chunks/c"
    history = _req(tc, "get", f"{base}/revisions", scope=["finance"])
    detail = _req(tc, "get", f"{base}/revisions/r1", scope=["finance"])
    diff = _req(
        tc, "get", f"{base}/revision-diff", scope=["finance"],
        params={"from_revision_id": "r1", "to_revision_id": "r2"},
    )
    assert history.status_code == detail.status_code == diff.status_code == 200
    assert history.json()["revisions"][0]["is_current"] is True
    assert detail.json()["revision"]["text"] == "old"
    assert diff.json()["from_revision_id"] == "r1"
    assert service.scopes == [frozenset({"finance"})] * 3


def test_no_mutation_verbs_on_read_surface(client):
    tc, _ = client
    assert _req(tc, "post", "/internal/mcp/v1/collections", scope=["finance"]).status_code == 405
    assert _req(tc, "delete", "/internal/mcp/v1/documents/x", scope=["finance"]).status_code == 405
    assert _req(tc, "put", "/internal/mcp/v1/documents/x/chunks?page=1").status_code == 405


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

    monkeypatch.setattr(im, "_internal_key", lambda: KEY)
    monkeypatch.setattr(im, "_client", lambda request: FakeErr())
    app = create_app()
    r = TestClient(app).request("get", "/internal/mcp/v1/documents/x",
                                headers={"X-API-Key": KEY})
    assert r.status_code == status
    assert r.json()["code"] == code
    assert "Traceback" not in r.text


def test_openapi_marks_internal_readonly(client):
    tc, _ = client
    schema = tc.get("/openapi.json").json()
    internal_paths = [p for p in schema["paths"] if p.startswith("/internal")]
    assert len(internal_paths) == 15
    for p in internal_paths:
        for method in schema["paths"][p]:
            assert method in ("get", "post", "parameters")
    assert schema["paths"]["/internal/mcp/v1/collections"]["get"]["tags"] == [
        "internal-mcp-readonly",
    ]
