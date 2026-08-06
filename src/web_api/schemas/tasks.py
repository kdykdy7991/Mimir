"""
DTOs for ``GET /tasks/{id}``.

First-version uses plain polling. SSE is a v2 candidate (see TODO M3 #18).

The task state types (``TaskStage`` / ``TaskStatus`` / ``TaskProgress`` /
``TaskError``) now live in the application layer
(``src.application.services.task_types``) so the app services don't
import the Web API; this module re-exports them for the response model.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.services.task_types import (  # noqa: F401
    TaskError,
    TaskProgress,
    TaskStage,
    TaskStatus,
)
from src.web_api.schemas._types import UtcDatetime


class TaskStatusResponse(BaseModel):
    """``GET /tasks/{id}`` response."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "1f2e3d4c-5b6a-4c8d-9e0f-1a2b3c4d5e6f",
                    "document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                    "collection_id": "b2c1f0e8-1234-5678-9abc-def012345678",
                    "status": "running",
                    "progress": {
                        "stage": "embed",
                        "current": 32,
                        "total": 100,
                        "percent": 32,
                        "message": "Embedding chunks",
                    },
                    "attempt": 0,
                    "error": None,
                    "created_at": "2026-07-31T08:23:11.234Z",
                    "updated_at": "2026-07-31T08:23:42.100Z",
                    "finished_at": None,
                },
                {
                    "id": "1f2e3d4c-5b6a-4c8d-9e0f-1a2b3c4d5e6f",
                    "document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                    "collection_id": "b2c1f0e8-1234-5678-9abc-def012345678",
                    "status": "succeeded",
                    "progress": {
                        "stage": "upsert",
                        "current": 142,
                        "total": 142,
                        "percent": 100,
                        "message": None,
                    },
                    "attempt": 0,
                    "error": None,
                    "created_at": "2026-07-31T08:23:11.234Z",
                    "updated_at": "2026-07-31T08:25:42.001Z",
                    "finished_at": "2026-07-31T08:25:42.001Z",
                },
                {
                    "id": "1f2e3d4c-5b6a-4c8d-9e0f-1a2b3c4d5e6f",
                    "document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                    "collection_id": "b2c1f0e8-1234-5678-9abc-def012345678",
                    "status": "failed",
                    "progress": {
                        "stage": "embed",
                        "current": 32,
                        "total": 100,
                        "percent": 32,
                        "message": "Embedding chunks",
                    },
                    "attempt": 0,
                    "error": {
                        "code": "UPSTREAM_ERROR",
                        "message": "Embedding provider failed",
                        "details": {"provider": "openai", "status_code": 502},
                    },
                    "created_at": "2026-07-31T08:23:11.234Z",
                    "updated_at": "2026-07-31T08:24:01.500Z",
                    "finished_at": "2026-07-31T08:24:01.500Z",
                },
            ]
        }
    )

    id: UUID
    document_id: UUID
    collection_id: UUID
    status: TaskStatus
    progress: TaskProgress | None = Field(
        None,
        description=(
            "Current stage + progress. ``None`` when status is ``pending`` "
            "or any terminal state."
        ),
    )
    attempt: int = Field(0, description="Retry attempt count (0 for first try).")
    error: TaskError | None = Field(
        None,
        description=(
            "Structured error when the task finished in a failed state. "
            "Same shape as the top-level HTTP error envelope — frontend "
            "branches on ``error.code``, never on ``error.message``. "
            "``None`` for any non-failed status."
        ),
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime
    finished_at: UtcDatetime | None = None


__all__ = [
    "TaskStage",
    "TaskStatus",
    "TaskProgress",
    "TaskStatusResponse",
]
