"""
``/collections/{id}/queries`` — the only way the Web UI calls the
retrieval pipeline.

M2 batch 3: wired to the real ``QueryService``. The query runs
synchronously (v0.2 decision — top_k=10 default P95 target < 2s); a
future M3 may task-ify it. ``query_id`` in the response equals the
trace id, so the UI can link to ``GET /queries/{query_id}/trace``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from src.application.composition import ApplicationServices
from src.libs.embedding import EmbeddingError
from src.web_api.dependencies import get_application_services
from src.web_api.errors import CollectionNotFoundError, UpstreamError
from src.web_api.mappers import resolve_collection_name, to_query_response
from src.web_api.schemas.queries import (
    AsyncQueryAccepted,
    QueryRequest,
    QueryResponse,
)

router = APIRouter(prefix="/collections", tags=["queries"])


@router.post(
    "/{collection_id}/queries",
    response_model=QueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Run a hybrid query against a collection",
    responses={
        404: {"description": "Collection not found."},
        502: {"description": "Embedding provider failed."},
    },
)
def create_query(
    body: QueryRequest,
    collection_id: UUID = Path(..., description="Collection ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> QueryResponse:
    """Run retrieval and return citations + diagnostics.

    M3: ``collection=name`` routes the query to the per-collection
    ``HybridSearch`` (EngineCache) — fixing the v0.2 limitation where
    every collection silently queried the default index.
    """
    name = resolve_collection_name(services, collection_id)
    if name is None:
        raise CollectionNotFoundError(
            f"collection {collection_id} does not exist",
            details={"collection_id": str(collection_id)},
        )

    try:
        result = services.query.search(
            query=body.query,
            top_k=body.top_k,
            mode=body.mode,
            collection=name,
        )
    except EmbeddingError as exc:
        raise UpstreamError(
            "embedding provider failed",
            details={"collection_id": str(collection_id)},
        ) from exc

    return to_query_response(result, collection=name)


@router.post(
    "/{collection_id}/queries/async",
    response_model=AsyncQueryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run a query asynchronously",
    responses={
        404: {"description": "Collection not found."},
    },
)
def create_async_query(
    body: QueryRequest,
    collection_id: UUID = Path(..., description="Collection ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> AsyncQueryAccepted:
    """Submit a query and return immediately.

    M3 batch 2: the query runs in a background thread; poll
    ``GET /queries/{query_id}/result`` for the outcome. ``task_id``
    equals ``query_id`` (and the trace id).
    """
    name = resolve_collection_name(services, collection_id)
    if name is None:
        raise CollectionNotFoundError(
            f"collection {collection_id} does not exist",
            details={"collection_id": str(collection_id)},
        )
    task_id = services.query.search_async(
        query=body.query,
        top_k=body.top_k,
        mode=body.mode,
        collection=name,
    )
    return AsyncQueryAccepted(query_id=task_id, task_id=task_id)


__all__ = ["router"]
