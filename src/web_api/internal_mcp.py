"""
Internal read-only MCP API for the main RAG server (Phase 4 §P4.1).

Only GET/POST *query* semantics are exposed — there are no mutation
endpoints here. Every handler reuses the SAME running
:class:`RagReadOnlyClient` (the in-process client over the application
services) so retrieval logic is never duplicated. The client re-validates
collection scope against the caller's principal instead of trusting a
client-supplied collection string.

Surface (mounted at ``/internal`` by ``create_app``):

    GET  /internal/mcp/v1/collections
    POST /internal/mcp/v1/query
    GET  /internal/mcp/v1/documents/{document_id}
    GET  /internal/mcp/v1/documents/{document_id}/chunks?page=1&page_size=20

Errors are flat ``{"code": "...", "message": "..."}`` bodies with stable
codes and never leak a Python stack trace back to the MCP caller.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.mcp_server.auth.context import TrustedLocalPrincipal
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

PRINCIPAL = TrustedLocalPrincipal()


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


@router.get("/collections")
def list_collections(request: Request) -> Any:
    client = _client(request)
    return _guarded(
        lambda: client.list_collections(PRINCIPAL),
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
    client = _client(request)
    model = QueryRequest(
        query=body.query,
        collection=body.collection,
        top_k=body.top_k,
        rerank=body.rerank,
    )
    return _guarded(
        lambda: client.query_knowledge(model, PRINCIPAL),
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
    client = _client(request)
    return _guarded(
        lambda: client.get_document(document_id, PRINCIPAL),
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
    client = _client(request)
    return _guarded(
        lambda: client.get_document_chunks(
            document_id, page, page_size, PRINCIPAL,
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