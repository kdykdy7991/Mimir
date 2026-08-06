"""
DTOs for the documents endpoints.

Mirrors the v2 API surface:
- ``GET    /collections/{id}/documents``
- ``POST   /collections/{id}/documents``
- ``GET    /documents/{id}``
- ``DELETE /documents/{id}``

For M1 these DTOs are the contract only; the upload path uses multipart,
M2 will switch to either multipart (sync) or a presigned-style flow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.services.task_types import TaskError
from src.application.services.upload_types import (  # noqa: F401  (re-exported)
    BatchFileResult,
    BatchFileStatus,
    BatchUploadResponse,
)
from src.web_api.schemas.common import Page
from src.web_api.schemas._types import UtcDatetime

DocumentStatus = Literal[
    "pending",     # created but not yet picked up
    "processing",  # ingestion running
    "ready",       # indexed and queryable
    "failed",      # terminal: last attempt failed
    "deleting",    # coordinated delete in progress
]
"""Document lifecycle states.

Kept distinct from :class:`TaskStatus` because one document may have
many ingestion tasks over its lifetime. See v0.1 contract §5.
"""


class DocumentSummary(BaseModel):
    """Lightweight row for collection document list."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                "collection_id": "b2c1f0e8-1234-5678-9abc-def012345678",
                "filename": "employee-handbook.pdf",
                "size_bytes": 1845632,
                "status": "ready",
                "chunk_count": 142,
                "image_count": 7,
                "created_at": "2026-07-31T08:23:11.234Z",
                "updated_at": "2026-07-31T08:25:42.001Z",
            }
        }
    )

    id: UUID
    collection_id: UUID
    filename: str = Field(..., description="Original filename.")
    size_bytes: int = Field(..., description="Raw file size in bytes.")
    status: DocumentStatus
    chunk_count: int = Field(
        0, description="Number of chunks written to the vector store.",
    )
    image_count: int = Field(
        0, description="Number of images extracted and indexed.",
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime


class DocumentDetail(DocumentSummary):
    """``GET /documents/{id}`` response — extends summary with ingestion refs.

    ``last_task_id`` lets the frontend jump straight to the task detail
    page when ``status`` is anything other than ``ready``.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                "collection_id": "b2c1f0e8-1234-5678-9abc-def012345678",
                "filename": "employee-handbook.pdf",
                "size_bytes": 1845632,
                "status": "ready",
                "chunk_count": 142,
                "image_count": 7,
                "created_at": "2026-07-31T08:23:11.234Z",
                "updated_at": "2026-07-31T08:25:42.001Z",
                "file_hash": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                "last_task_id": "1f2e3d4c-5b6a-4c8d-9e0f-1a2b3c4d5e6f",
                "last_query_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                "last_error": None,
            }
        }
    )

    file_hash: str | None = Field(
        None,
        description="SHA-256 of the raw file. Used to detect duplicate uploads.",
    )
    last_task_id: UUID | None = Field(
        None, description="Most recent ingestion task ID, if any.",
    )
    last_query_id: UUID | None = Field(
        None,
        description=(
            "Most recent query that cited this document, if any (M3 batch 2). "
            "Lets the UI jump from a document back to the query's trace."
        ),
    )
    last_error: TaskError | None = Field(
        None,
        description=(
            "Structured error from the last failed task (if any). Same shape "
            "as the top-level HTTP error envelope — branch on ``last_error.code``."
        ),
    )


class DocumentListResponse(Page[DocumentSummary]):
    """``GET /collections/{id}/documents`` response.

    Example (empty collection)::

        {
          "items": [],
          "page_info": {"next_cursor": null, "has_more": false}
        }
    """


class DocumentUploadResponse(BaseModel):
    """``POST /collections/{id}/documents`` response.

    The synchronous-first design returns the created document + the
    ingestion task ID immediately. The frontend then polls ``/tasks/{id}``
    for status (no SSE in M1 — see TODO M3 #18).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document": {
                    "id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                    "collection_id": "b2c1f0e8-1234-5678-9abc-def012345678",
                    "filename": "q2-roadmap.pdf",
                    "size_bytes": 524288,
                    "status": "pending",
                    "chunk_count": 0,
                    "image_count": 0,
                    "created_at": "2026-07-31T08:23:11.234Z",
                    "updated_at": "2026-07-31T08:23:11.234Z",
                },
                "task_id": "1f2e3d4c-5b6a-4c8d-9e0f-1a2b3c4d5e6f",
            }
        }
    )

    document: DocumentDetail
    task_id: UUID = Field(
        ..., description="Ingestion task ID; poll ``GET /tasks/{id}`` for status.",
    )


# Batch-upload DTOs (``BatchFileStatus`` / ``BatchFileResult`` /
# ``BatchUploadResponse``) live in ``src.application.services.upload_types``
# (M5 review: the application layer must not import the Web API). They are
# imported at the top of this module and re-exported via ``__all__`` so the
# router's ``response_model`` and the generated OpenAPI stay unchanged.


__all__ = [
    "BatchFileResult",
    "BatchFileStatus",
    "BatchUploadResponse",
    "DocumentStatus",
    "DocumentSummary",
    "DocumentDetail",
    "DocumentListResponse",
    "DocumentUploadResponse",
]
