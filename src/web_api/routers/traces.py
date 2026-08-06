"""
``/queries/{id}/trace`` and ``/ingestions/{id}/trace`` — both return the
same :class:`TraceResponse` shape so the Web UI can render one component.

M2 batch 3: reads through the shared :class:`TraceStore` (in-memory
index + JSONL). For ingestions, a task that exists in the ``TaskTracker``
but never recorded a trace still returns 200 with empty ``stages`` (the
record exists; the trace was lost / never enabled) rather than 404.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path

from src.application.composition import ApplicationServices
from src.web_api.dependencies import get_application_services
from src.web_api.errors import IngestionNotFoundError, QueryNotFoundError
from src.web_api.mappers import to_query_response, to_trace_response
from src.web_api.schemas.queries import AsyncQueryResult
from src.web_api.schemas.traces import TraceResponse

router = APIRouter(tags=["traces"])


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
    trace is missing but the task still exists (e.g. recorded before this
    feature, or trace persistence failed), return 200 with empty stages —
    the ingestion *record* is real even if its timeline is gone.
    """
    raw = services.trace.get(ingestion_id)
    if raw is not None:
        return to_trace_response(raw)

    try:
        task_id = UUID(ingestion_id)
    except ValueError:
        task_id = None

    task = services.ingestion.get_task(task_id) if task_id is not None else None
    if task is None:
        raise IngestionNotFoundError(
            f"ingestion {ingestion_id!r} does not exist",
            details={"ingestion_id": ingestion_id},
        )
    return _empty_task_trace(task)


def _empty_task_trace(task) -> TraceResponse:
    """Build a 200 TraceResponse from a task record that has no trace."""
    started_at = task.created_at
    end = task.finished_at or task.updated_at or task.created_at
    total_ms = max(0.0, (end - started_at).total_seconds() * 1000.0)
    return TraceResponse(
        id=task.id,
        trace_type="ingestion",
        started_at=started_at,
        finished_at=end,
        total_latency_ms=total_ms,
        stages=[],
        error=task.error.message if task.error is not None else None,
    )


__all__ = ["router"]
