"""
``/tasks/{id}`` - ingestion task status.

M2 batch 2: wired to ``IngestionService.get_task``. The endpoint reads
the live snapshot from the in-memory :class:`TaskTracker` and maps it
to the v0.1 ``TaskStatusResponse`` schema. Polling is the contract;
SSE is a v2 candidate (see TODO M3 #18).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path

from src.application.composition import ApplicationServices
from src.web_api.dependencies import get_application_services
from src.web_api.errors import TaskNotFoundError
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


__all__ = ["router"]
