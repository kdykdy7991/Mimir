"""
``/tasks/{id}`` - ingestion task status.

M2 batch 2: wired to ``IngestionService.get_task``. The endpoint reads
the live snapshot from the in-memory :class:`TaskTracker` and maps it
to the v0.1 ``TaskStatusResponse`` schema. Polling is the contract;
SSE is a v2 candidate (see TODO M3 #18).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from src.application.composition import ApplicationServices
from src.application.services.ingestion_service import RetrySourceMissingError
from src.web_api.dependencies import get_application_services
from src.web_api.errors import (
    SourceFileMissingError,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskNotRetryableError,
)
from src.web_api.schemas.errors import TaskError
from src.web_api.schemas.tasks import TaskProgress, TaskStatusResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get(
    "/{task_id}",
    response_model=TaskStatusResponse,
    summary="Get ingestion task status",
)
async def get_task(
    task_id: UUID = Path(..., description="Ingestion task ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> TaskStatusResponse:
    """Return the current status of an ingestion task.

    The task registry is process-local. A task that cannot be found
    returns ``404`` with envelope code ``TASK_NOT_FOUND``. Frontends
    poll this endpoint after ``POST /collections/{id}/documents`` until
    the status reaches ``succeeded`` or ``failed``.
    """
    record = services.ingestion.get_task(task_id)
    if record is None or record.task_type != "ingestion":
        raise TaskNotFoundError(
            f"task {task_id} does not exist",
            details={"task_id": str(task_id)},
        )
    return _to_status_response(record)


def _to_status_response(record) -> TaskStatusResponse:
    """Map an ingestion ``TaskRecord`` to the v0.1 status DTO."""
    return TaskStatusResponse(
        id=record.id,
        document_id=record.document_id,
        collection_id=record.collection_id,
        status=record.status,
        progress=record.progress,
        attempt=record.attempt,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
        finished_at=record.finished_at,
    )


@router.post(
    "/{task_id}/retry",
    response_model=TaskStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed or cancelled ingestion task",
)
async def retry_task(
    task_id: UUID = Path(..., description="Ingestion task ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> TaskStatusResponse:
    """Create a NEW ingestion task + trace retrying ``task_id``.

    Only ``failed`` / ``cancelled`` tasks may be retried — any other state
    returns a stable ``409``. The original task + trace are left intact; a
    fresh task with ``attempt == parent.attempt + 1`` and a
    ``parent_trace_id`` pointing at the original is created, reusing the
    original document source + parsing config. Retries are idempotent: a
    duplicate/concurrent retry of the same task returns the already-created
    child instead of spawning another. Returns ``202`` with the child task's
    status (usually ``pending``, ready to poll).
    """
    record = services.ingestion.get_task(task_id)
    if record is None or record.task_type != "ingestion":
        raise TaskNotFoundError(
            f"task {task_id} does not exist",
            details={"task_id": str(task_id)},
        )
    if record.status not in ("failed", "cancelled"):
        raise TaskNotRetryableError(
            f"task {task_id} is in state {record.status!r}; only failed or "
            "cancelled tasks can be retried",
            details={"task_id": str(task_id), "status": record.status},
        )
    try:
        child = services.ingestion.retry(task_id)
    except RetrySourceMissingError as exc:
        raise SourceFileMissingError(
            f"original source file for task {task_id} is missing",
            details={"task_id": str(task_id), "source_path": str(exc)},
        ) from exc
    if child is None:
        raise TaskNotRetryableError(
            f"task {task_id} cannot be retried in its current state",
            details={"task_id": str(task_id)},
        )
    return _to_status_response(child)


@router.post(
    "/{task_id}/cancel",
    response_model=TaskStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cooperatively cancel an ingestion task",
)
async def cancel_task(
    task_id: UUID = Path(..., description="Ingestion task ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> TaskStatusResponse:
    """Request cooperative cancellation of an ingestion task.

    Only ``pending`` / ``running`` tasks may be cancelled; any other
    terminal state returns a stable ``409``. The worker stops at the next
    safe stage boundary, marks the task ``cancelled`` and records a terminal
    ``canceled`` trace — completed atomic writes are never rolled back and
    existing documents / old indexes are kept. Repeated cancels are
    idempotent while the task is still in flight. Returns ``202`` with the
    resulting task status.
    """
    record = services.ingestion.get_task(task_id)
    if record is None or record.task_type != "ingestion":
        raise TaskNotFoundError(
            f"task {task_id} does not exist",
            details={"task_id": str(task_id)},
        )
    if record.status in ("succeeded", "failed", "cancelled", "skipped"):
        raise TaskNotCancelableError(
            f"task {task_id} is in terminal state {record.status!r} ",
            details={"task_id": str(task_id), "status": record.status},
        )
    cancelled = services.ingestion.cancel(task_id)
    if cancelled is None:
        raise TaskNotFoundError(
            f"task {task_id} does not exist",
            details={"task_id": str(task_id)},
        )
    return _to_status_response(cancelled)


__all__ = ["router"]
