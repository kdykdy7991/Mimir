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

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.mcp_server.clients.errors import (
    AccessDeniedError,
    InvalidRequestError,
    ResourceNotFoundError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
from src.mcp_server.clients.models import QueryRequest
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


__all__ = ["router"]