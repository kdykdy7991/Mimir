"""
Internal read-only MCP API for the main RAG server (Phase 4 §P4.1).

Review-fix (P0 #1/#2): this surface is AUTHENTICATED and SCOPED.

- **Authentication**: every request must carry ``X-API-Key`` equal to the
  configured ``mcp_server.api_key`` (core Settings). Missing/wrong key →
  401/403. If no key is configured the API fails closed (403), so it can
  never be reached without an explicit deploy-time secret. Being mounted
  under ``/internal`` is not the security boundary — the key check is.
- **Authorisation scope**: the MCP aggregator forwards the caller's
  collection grants in ``X-MCP-Allowed-Collections`` (comma-separated,
  authenticated by the shared key). The internal API builds a scoped
  principal from it and the SAME ``RagReadOnlyClient`` re-validates every
  collection access (``filter_accessible_collections`` /
  ``require_collection_access``). A missing scope header yields a
  deny-all principal — the caller can never read outside a claimed scope.
  ``TrustedLocalPrincipal`` is NEVER used on this endpoint.

Surface (mounted at ``/internal`` by ``create_app``):

    GET  /internal/mcp/v1/collections
    POST /internal/mcp/v1/query
    GET  /internal/mcp/v1/documents/{document_id}
    GET  /internal/mcp/v1/documents/{document_id}/chunks?page=1&page_size=20

Errors are flat ``{"code": "...", "message": "..."}`` bodies with stable
codes and never leak a Python stack trace.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.mcp_server.clients.errors import (
    AccessDeniedError,
    InvalidRequestError,
    ResourceNotFoundError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
from src.application.contracts import ChunkContextRequest, SearchRequest, to_jsonable
from src.application.services.revision_service import (
    RevisionNotFoundOrAccessibleError,
    RevisionService,
)
from src.ingestion.storage import RevisionStore
from src.mcp_server.clients.models import DocumentListRequest, QueryRequest
from src.web_api.dependencies import DEFAULT_DATA_DIR, get_application_services

router = APIRouter(prefix="/internal/mcp/v1", tags=["internal-mcp-readonly"])

_API_KEY_HEADER = "X-API-Key"
_SCOPE_HEADER = "X-MCP-Allowed-Collections"


@lru_cache(maxsize=1)
def _internal_key() -> str:
    """The configured internal service credential (core Settings)."""
    from src.core.settings import load_settings

    try:
        return load_settings("./config/settings.yaml").mcp_server.api_key or ""
    except Exception:  # noqa: BLE001
        return ""


@dataclass(frozen=True)
class _ScopedPrincipal:
    """Minimal :class:`CollectionPrincipal` built from an authenticated request."""

    key_id: str
    name: str
    allowed_collections: frozenset[str]


def _require_internal_key(request: Request) -> JSONResponse | None:
    """Authenticate the request; returns a flat 401/503 JSONResponse if it
    fails, or ``None`` to allow the handler to proceed."""
    key = _internal_key()
    if not key:
        return JSONResponse(
            status_code=503,
            content={"code": "internal_api_not_configured",
                     "message": "internal API key is not configured; server refuses"},
        )
    supplied = request.headers.get(_API_KEY_HEADER, "")
    if supplied != key:
        return JSONResponse(
            status_code=401,
            content={"code": "internal_unauthorized",
                     "message": "invalid or missing internal API key"},
        )
    return None


def _principal_from_request(request: Request) -> _ScopedPrincipal:
    """Derive a strictly-scoped principal; missing/invalid scope ⇒ deny-all."""
    from src.mcp_server.clients.scope import decode_scope_header

    raw = request.headers.get(_SCOPE_HEADER, "")
    allowed: frozenset[str] = frozenset()
    if raw:
        try:
            allowed = frozenset(decode_scope_header(raw))
        except (ValueError, TypeError):
            # Malformed scope never widens privileges — fail closed to the
            # caller's own (deny-all) view.
            allowed = frozenset()
    return _ScopedPrincipal(
        key_id="internal-mcp", name="internal-mcp", allowed_collections=allowed,
    )


def _client(request: Request) -> InProcessRagReadOnlyClient:
    """Reuse the boot application services; never copy retrieval logic."""
    services = get_application_services(request)
    return InProcessRagReadOnlyClient(
        data_dir=DEFAULT_DATA_DIR,
        services=services,  # type: ignore[arg-type]
    )


def _error_response(exc: Exception) -> JSONResponse:
    """Stable-code flat error body (no Python stack)."""
    for status, code, typ in [
        (404, "not_found", RevisionNotFoundOrAccessibleError),
        (404, "not_found", ResourceNotFoundError),
        (403, "access_denied", AccessDeniedError),
        (400, "invalid_request", InvalidRequestError),
        (504, "upstream_timeout", UpstreamTimeoutError),
        (503, "upstream_unavailable", UpstreamUnavailableError),
    ]:
        if isinstance(exc, typ):
            return JSONResponse(
                status_code=status,
                content={"code": code, "message": str(exc)},
            )
    return JSONResponse(
        status_code=500,
        content={"code": "internal", "message": "internal error"},
    )


def _revision_service(request: Request) -> RevisionService:
    client = _client(request)
    store = RevisionStore(Path(DEFAULT_DATA_DIR) / "db" / "revisions.db")
    return RevisionService(store, client.get_authorized_chunk_snapshot)


def _guarded(fn: Callable[[], Any], ok: Callable[[Any], dict]) -> Any:
    """Run a read, map client errors to stable flat bodies."""
    try:
        return ok(fn())
    except Exception as exc:  # noqa: BLE001
        return _error_response(exc)


def _gate(request: Request) -> JSONResponse | None:
    """Authenticate; return the flat error response, or None to proceed."""
    auth = _require_internal_key(request)
    if auth is not None:
        return auth
    return None


@router.get("/collections")
def list_collections(request: Request) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    client = _client(request)
    return _guarded(
        lambda: client.list_collections(principal),
        lambda items: {
            "count": len(items),
            "collections": [
                {
                    "name": c.name,
                    "description": c.description,
                    "document_count": c.document_count,
                    "chunk_count": c.chunk_count,
                }
                for c in items
            ],
        },
    )


class _QueryBody(BaseModel):
    query: str
    collection: str = "default"
    top_k: int = 10
    rerank: bool = True


class _SearchBody(BaseModel):
    query: str
    collection: str | None = None
    alternate_queries: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)
    failure_policy: str = "fail_fast"
    mode: str = "hybrid"
    filters: dict[str, Any] | None = None
    top_k: int = 10
    rerank: bool = True
    threshold: float | None = None
    include_content: bool = True


@router.post("/search")
def search_chunks(body: _SearchBody, request: Request) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    try:
        model = SearchRequest(**body.model_dump())
    except Exception as exc:  # contract validation
        return _error_response(InvalidRequestError(str(exc)))
    client = _client(request)
    return _guarded(
        lambda: client.search(model, principal),
        lambda result: to_jsonable(result),
    )


class _ChunkContextBody(BaseModel):
    include: str = "both"
    before: int = 1
    after: int = 1
    max_chars: int = 12_000


@router.post("/documents/{document_id}/chunks/{chunk_id}/context")
def get_chunk_context(
    document_id: str, chunk_id: str, body: _ChunkContextBody, request: Request,
) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    try:
        model = ChunkContextRequest(
            document_id=document_id, chunk_id=chunk_id, **body.model_dump(),
        )
    except Exception as exc:
        return _error_response(InvalidRequestError(str(exc)))
    return _guarded(
        lambda: _client(request).get_chunk_context(model, principal),
        lambda result: to_jsonable(result),
    )


@router.get("/documents/{document_id}/assets/{asset_id}")
def get_asset(document_id: str, asset_id: str, request: Request) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    return _guarded(
        lambda: _client(request).get_asset(document_id, asset_id, principal),
        lambda result: {
            "metadata": to_jsonable(result.metadata),
            "data_base64": base64.b64encode(result.data).decode("ascii"),
        },
    )


@router.post("/query")
def query_knowledge(body: _QueryBody, request: Request) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    # The read client's query path authorises collection scope the same way
    # the MCP tool does (resolve + validate), so an out-of-scope collection
    # here is a stable 403, never a probe into upstream.
    from src.mcp_server.auth.authorization import (
        CollectionAccessDenied,
        CollectionSelectionRequired,
        resolve_query_collection,
    )

    try:
        collection = resolve_query_collection(principal, body.collection)
    except (CollectionAccessDenied, CollectionSelectionRequired) as exc:
        return _error_response(AccessDeniedError(str(exc)))
    client = _client(request)
    model = QueryRequest(
        query=body.query,
        collection=collection,
        top_k=body.top_k,
        rerank=body.rerank,
    )
    return _guarded(
        lambda: client.query_knowledge(model, principal),
        lambda result: {
            "query": result.query,
            "collection": result.collection,
            "count": result.count,
            "evidence": [
                {
                    "rank": e.rank,
                    "chunk_id": e.chunk_id,
                    "document_id": e.document_id,
                    "title": e.title,
                    "source": e.source,
                    "page": e.page,
                    "score": e.score,
                    "text": e.text,
                }
                for e in result.evidence
            ],
            "diagnostics": {
                "degraded": result.diagnostics.degraded,
                "reasons": list(result.diagnostics.reasons),
                "trace_id": result.diagnostics.trace_id,
            },
        },
    )


@router.get("/documents/{document_id}")
def get_document(document_id: str, request: Request) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    client = _client(request)
    return _guarded(
        lambda: client.get_document(document_id, principal),
        lambda info: {
            "document_id": info.document_id,
            "collection": info.collection,
            "title": info.title,
            "document_type": info.document_type,
            "source": info.source,
            "summary": info.summary,
            "tags": list(info.tags),
            "chunk_count": int(info.chunk_count),
            "version": info.version, "folder_id": info.folder_id,
            "parser": info.parser, "index_status": info.index_status,
            "content_type": info.content_type, "updated_at": info.updated_at,
        },
    )


@router.get("/documents")
def list_documents(
    request: Request,
    collection: str,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    status: str | None = None,
    file_type: str | None = None,
    folder_id: str | None = None,
    tag_id: list[str] = Query(default=[]),
    tag_operator: str = "and",
    updated_after: float | None = None,
    updated_before: float | None = None,
    sort: str = "updated_desc",
) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    client = _client(request)
    model = DocumentListRequest(
        collection=collection, page=page, page_size=page_size, q=q,
        status=status, file_type=file_type, folder_id=folder_id,
        tag_ids=tuple(tag_id), tag_operator=tag_operator,
        updated_after=updated_after, updated_before=updated_before, sort=sort,
    )
    return _guarded(
        lambda: client.list_documents(model, principal),
        lambda result: {
            "collection": result.collection, "page": result.page,
            "page_size": result.page_size, "total": result.total,
            "has_next": result.has_next,
            "documents": [
                {
                    "document_id": d.document_id, "collection": d.collection,
                    "title": d.title, "document_type": d.document_type,
                    "source": d.source, "status": d.status,
                    "chunk_count": d.chunk_count, "image_count": d.image_count,
                    "tags": list(d.tags), "folder_id": d.folder_id,
                    "created_at": d.created_at, "updated_at": d.updated_at,
                } for d in result.documents
            ],
        },
    )


@router.get("/documents/{document_id}/chunks")
def get_document_chunks(
    document_id: str,
    page: int = 1,
    page_size: int = 20,
    request: Request = None,  # type: ignore[assignment]
) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    client = _client(request)
    return _guarded(
        lambda: client.get_document_chunks(
            document_id, page, page_size, principal,
        ),
        lambda result: {
            "document_id": result.document_id,
            "page": result.page,
            "page_size": result.page_size,
            "total": result.total,
            "has_next": result.has_next,
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "index": c.index,
                    "text": c.text,
                    "page": c.page,
                    "section": c.section,
                }
                for c in result.chunks
            ],
        },
    )


@router.get("/documents/{document_id}/chunks/{chunk_id}")
def get_chunk(document_id: str, chunk_id: str, request: Request) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    client = _client(request)
    return _guarded(
        lambda: client.get_chunk(document_id, chunk_id, principal),
        lambda c: {
            "document_id": c.document_id, "chunk_id": c.chunk_id,
            "index": c.index, "text": c.text, "heading": c.heading,
            "page": c.page, "content_type": c.content_type,
            "previous_chunk_id": c.previous_chunk_id,
            "next_chunk_id": c.next_chunk_id, "parent_id": c.parent_id,
            "source_locator": c.source_locator, "asset_ids": list(c.asset_ids),
            "document_version": c.document_version,
            "chunk_version": c.chunk_version, "is_current": c.is_current,
        },
    )


@router.get("/documents/{document_id}/chunks/{chunk_id}/revisions")
def list_chunk_revisions(
    document_id: str, chunk_id: str, request: Request,
) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    service = _revision_service(request)
    return _guarded(
        lambda: service.list_history(document_id, chunk_id, principal),
        lambda rows: {"revisions": to_jsonable(rows), "count": len(rows)},
    )


@router.get("/documents/{document_id}/chunks/{chunk_id}/revisions/{revision_id}")
def get_chunk_revision(
    document_id: str, chunk_id: str, revision_id: str, request: Request,
) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    service = _revision_service(request)
    return _guarded(
        lambda: service.get_revision(
            document_id, chunk_id, revision_id, principal,
        ),
        lambda result: {
            "revision": to_jsonable(result[0]),
            "status": result[1].value,
            "is_current": result[2],
        },
    )


@router.get("/documents/{document_id}/chunks/{chunk_id}/revision-diff")
def diff_chunk_revisions(
    document_id: str,
    chunk_id: str,
    request: Request,
    from_revision_id: str,
    to_revision_id: str,
) -> Any:
    gate = _gate(request)
    if gate is not None:
        return gate
    principal = _principal_from_request(request)
    service = _revision_service(request)
    return _guarded(
        lambda: service.diff(
            document_id, chunk_id, from_revision_id, to_revision_id, principal,
        ),
        lambda result: to_jsonable(result),
    )


def _source_view(source) -> dict[str, Any]:
    return {
        "id": source.id, "name": source.name,
        "connector_type": source.connector_type,
        "collection_id": source.collection_id, "enabled": source.enabled,
        "checkpoint_revision": source.checkpoint_revision,
        "updated_at": source.updated_at,
    }


@router.get("/data-sources")
def list_data_sources(request: Request) -> Any:
    gate = _gate(request)
    if gate is not None: return gate
    items = _client(request).list_data_sources(_principal_from_request(request))
    return {"count": len(items), "data_sources": [_source_view(item) for item in items]}


@router.get("/data-sources/{source_id}/status")
def get_sync_status(source_id: str, request: Request) -> Any:
    gate = _gate(request)
    if gate is not None: return gate
    return _guarded(
        lambda: _client(request).get_sync_status(source_id, _principal_from_request(request)),
        lambda value: {"data_source": _source_view(value.data_source), "last_run": value.last_run},
    )


@router.get("/data-sources/{source_id}/failures")
def list_sync_failures(source_id: str, request: Request, limit: int = Query(50, ge=1, le=100)) -> Any:
    gate = _gate(request)
    if gate is not None: return gate
    return _guarded(
        lambda: _client(request).list_sync_failures(source_id, limit, _principal_from_request(request)),
        lambda items: {"count": len(items), "failures": items},
    )


__all__ = ["router"]
