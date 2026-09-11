"""
``/queries/{id}/trace`` and ``/ingestions/{id}/trace`` — both return the
same :class:`TraceResponse` shape so the Web UI can render one component.

M2 batch 3: reads through the shared :class:`TraceStore` (in-memory
index + JSONL). For ingestions, a task that exists in the ``TaskTracker``
but never recorded a trace still returns 200 with empty ``stages`` (the
record exists; the trace was lost / never enabled) rather than 404.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from src.application.composition import ApplicationServices
from src.web_api.dependencies import get_application_services
from src.web_api.errors import IngestionNotFoundError, QueryNotFoundError
from src.web_api.mappers import (
    build_live_trace_response,
    to_query_response,
    to_trace_response,
)
from src.web_api.schemas.common import PageInfo
from src.web_api.schemas.queries import AsyncQueryResult
from src.web_api.schemas.traces import TraceListResponse, TraceResponse

router = APIRouter(tags=["traces"])


@router.get(
    "/traces",
    response_model=TraceListResponse,
    summary="List traces",
)
def list_traces(
    trace_type: str | None = Query(
        None, alias="type", description="Filter by trace type ('query' | 'ingestion').",
    ),
    status: str | None = Query(
        None, description="Filter by trace status (e.g. 'success', 'failed', 'canceled').",
    ),
    collection_id: UUID | None = Query(
        None, description="Filter ingestion traces for a collection.",
    ),
    document_id: UUID | None = Query(
        None, description="Filter ingestion traces for a document.",
    ),
    q: str | None = Query(
        None, description="Full-text filter over trace id, collection/doc ids, stage names.",
    ),
    started_from: datetime | None = Query(
        None, alias="from", description="Exclusive-ish lower bound on trace start (ISO-8601).",
    ),
    started_to: datetime | None = Query(
        None, alias="to", description="Upper bound on trace start (ISO-8601).",
    ),
    cursor: str | None = Query(
        None, description="Opaque cursor from the previous page's ``next_cursor``.",
    ),
    limit: int = Query(20, ge=1, le=200, description="Page size."),
    services: ApplicationServices = Depends(get_application_services),
) -> TraceListResponse:
    """Bounded, newest-first trace listing backed by the SQLite index.

    Filtering + pagination happen in the store's index, so this never scans
    the unbounded JSONL. The cursor is a stable ``(started_at, trace_id)``
    keyset — pages do not shift when new traces are recorded in between.
    """
    items, next_cursor = services.trace.list(
        trace_type=trace_type,
        status=status,
        collection_id=str(collection_id) if collection_id else None,
        document_id=str(document_id) if document_id else None,
        q=q,
        started_from=_to_epoch(started_from),
        started_to=_to_epoch(started_to),
        cursor=cursor,
        limit=limit,
    )
    traces = [to_trace_response(raw) for raw in items]
    return TraceListResponse(
        items=traces,
        page_info=PageInfo(
            next_cursor=next_cursor,
            has_more=next_cursor is not None,
        ),
    )


def _to_epoch(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


@router.get(
    "/queries/{query_id}/result",
    response_model=AsyncQueryResult,
    summary="Get an async query result",
)
def get_async_query_result(
    query_id: UUID = Path(..., description="Query ID (== task id)."),
    services: ApplicationServices = Depends(get_application_services),
) -> AsyncQueryResult:
    """Poll an async query (M3 batch 2).

    ``pending`` / ``running`` → keep polling; ``succeeded`` populates
    ``result``; ``failed`` populates ``error``. Unknown ids return 404
    ``QUERY_NOT_FOUND``.
    """
    state = services.query.get_async_result(query_id)
    if state is None:
        raise QueryNotFoundError(
            f"query {query_id} does not exist",
            details={"query_id": str(query_id)},
        )
    result = None
    if state.result is not None and state.collection:
        result = to_query_response(state.result, state.collection)
    return AsyncQueryResult(
        status=state.status,
        query_id=query_id,
        result=result,
        error=state.error,
    )


@router.get(
    "/queries/{query_id}/trace",
    response_model=TraceResponse,
    summary="Get a query trace",
)
def get_query_trace(
    query_id: str = Path(..., description="Query ID (== trace id)."),
    services: ApplicationServices = Depends(get_application_services),
) -> TraceResponse:
    """Return the per-stage trace for a previously run query.

    Every query recorded by the Web API stores its trace under
    ``query_id == trace_id``, so this is a straight store lookup.
    """
    raw = services.trace.get(query_id)
    if raw is None:
        raise QueryNotFoundError(
            f"query {query_id!r} does not exist",
            details={"query_id": query_id},
        )
    return to_trace_response(raw)


@router.get(
    "/ingestions/{ingestion_id}/trace",
    response_model=TraceResponse,
    summary="Get an ingestion trace",
)
def get_ingestion_trace(
    ingestion_id: str = Path(..., description="Ingestion ID (== task id)."),
    services: ApplicationServices = Depends(get_application_services),
) -> TraceResponse:
    """Return the per-stage trace for a previously run ingestion.

    Ingestion traces are recorded with ``trace_id == task_id``. When the
    task is mid-flight its live snapshot (completed stages + current stage)
    is merged with any recorded trace; when the trace is missing but the
    task still exists (e.g. recorded before this feature, or trace
    persistence failed), return 200 with empty stages — the ingestion
    *record* is real even if its timeline is gone. Terminal/final state
    wins and never regresses.
    """
    raw = services.trace.get(ingestion_id)
    try:
        task_id = UUID(ingestion_id)
    except ValueError:
        task_id = None
    task = services.ingestion.get_task(task_id) if task_id is not None else None

    # A real (persisted/live) trace + the task is the merge baseline.
    if raw is not None and task is not None:
        return build_live_trace_response(task, raw)
    if raw is not None:
        return to_trace_response(raw)
    if task is None:
        raise IngestionNotFoundError(
            f"ingestion {ingestion_id!r} does not exist",
            details={"ingestion_id": ingestion_id},
        )
    return build_live_trace_response(task, None)


__all__ = ["router"]
