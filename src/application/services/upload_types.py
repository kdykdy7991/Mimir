"""
Batch-upload DTOs and the upload limit policy for the application layer.

Moved out of ``src.web_api.schemas`` / ``src.web_api.settings`` (M5
review): ``IngestionService`` is reused by CLI / MCP / Streamlit / Web
API and must not import the Web API layer. The Web API schemas re-export
these Pydantic models so FastAPI response models / OpenAPI stay
unchanged, and the Web API boot builds an :class:`UploadPolicy` from its
env-driven settings and injects it into the service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.services.task_types import TaskError


# ---------------------------------------------------------------------------
# Upload limit policy — injected into IngestionService (no web_api import)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UploadPolicy:
    """Server-side upload limits (per file, per batch, format allow-lists).

    Defaults mirror the historical ``src.web_api.settings`` values. The
    Web API boot overrides them from ``WEB_API_*`` env vars via its own
    ``SETTINGS``; CLI / MCP / tests keep the defaults.
    """

    max_file_bytes: int = 30 * 1024 * 1024  # 30 MB per file
    allowed_extensions: FrozenSet[str] = field(
        default_factory=lambda: frozenset({
            ".pdf", ".md", ".markdown", ".docx", ".csv", ".xlsx", ".pptx",
            ".doc", ".xls", ".ppt",
            ".txt", ".html", ".htm", ".mhtml", ".mht",
            ".epub", ".xmind",
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
        }),
    )
    allowed_mime: FrozenSet[str] = field(
        default_factory=lambda: frozenset({
            "application/pdf",
            "text/markdown",
            "text/plain",
            "text/csv",
            "application/csv",
            "text/html",
            "application/epub+zip",
            "application/xmind",
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
            "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }),
    )
    max_batch_files: int = 100
    max_batch_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GB total per batch


# ---------------------------------------------------------------------------
# Batch upload input (service API)
# ---------------------------------------------------------------------------

@dataclass
class BatchFileUpload:
    """One validated file to submit inside a batch (M5).

    ``content_type`` is the multipart part's MIME type (transport-level);
    the service enforces the strict extension + MIME double validation
    and per-file size cap before any task is created.
    """

    filename: str
    content_type: str | None
    bytes_payload: bytes
    size_bytes: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.size_bytes = len(self.bytes_payload)


# ---------------------------------------------------------------------------
# Batch upload response DTOs
# ---------------------------------------------------------------------------

BatchFileStatus = Literal[
    "accepted",   # task created; poll ``task_id`` for the final status
    "skipped",    # duplicate of an already-ingested file; no task created
    "rejected",   # failed boundary validation (extension/MIME/size/dupe name)
]
"""Submission-time status of one file inside a batch (M5).

Distinct from :class:`TaskStatus`: ``accepted`` files keep evolving
asynchronously (poll ``GET /tasks/{task_id}``); ``skipped``/``rejected``
are terminal at submission time.
"""


class BatchFileResult(BaseModel):
    """One file's result inside a ``BatchUploadResponse`` (M5)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "filename": "employee-handbook.pdf",
                "document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                "task_id": "1f2e3d4c-5b6a-4c8d-9e0f-1a2b3c4d5e6f",
                "status": "accepted",
                "size_bytes": 1845632,
                "error": None,
            }
        }
    )

    filename: str = Field(..., description="Original file name.")
    document_id: UUID | None = Field(
        None,
        description=(
            "Stable document UUID (``uuid5`` of collection+source path). "
            "``None`` for rejected files (never written to storage)."
        ),
    )
    task_id: UUID | None = Field(
        None,
        description=(
            "Ingestion task to poll for ``accepted`` files; ``None`` for "
            "``skipped``/``rejected``."
        ),
    )
    status: BatchFileStatus
    size_bytes: int = Field(0, description="Raw file size in bytes.")
    error: TaskError | None = Field(
        None,
        description=(
            "Structured error for ``rejected`` files. Same shape as the "
            "HTTP error envelope — branch on ``error.code`` "
            "(``UNSUPPORTED_FILE_TYPE`` / ``UNSUPPORTED_MEDIA_TYPE`` / "
            "``FILE_TOO_LARGE`` / ``DUPLICATE_FILENAME``)."
        ),
    )


class BatchUploadResponse(BaseModel):
    """``POST /collections/{id}/documents`` batch response (M5).

    One batch uploads many files to the same collection. Each file is
    validated and submitted independently — a single rejection never
    blocks the others, and accepted files each carry their own
    ``task_id`` for async polling. ``total == accepted + skipped + rejected``.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "batch_id": "6c9f3d5a-8b1e-4a2d-9c7f-0e5d4a3b2c1a",
                "collection_id": "b2c1f0e8-1234-5678-9abc-def012345678",
                "total": 3,
                "accepted": 2,
                "skipped": 0,
                "rejected": 1,
                "files": [
                    {
                        "filename": "a.pdf",
                        "document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                        "task_id": "1f2e3d4c-5b6a-4c8d-9e0f-1a2b3c4d5e6f",
                        "status": "accepted",
                        "size_bytes": 1024,
                        "error": None,
                    },
                    {
                        "filename": "b.md",
                        "document_id": "8e5b2d4f-3c1e-4b3f-9d0e-2b3c4d5e6f7a",
                        "task_id": "2f3e4d5c-6b7a-4c9d-8e0f-3b4c5d6e7f8b",
                        "status": "accepted",
                        "size_bytes": 2048,
                        "error": None,
                    },
                    {
                        "filename": "bad.txt",
                        "document_id": None,
                        "task_id": None,
                        "status": "rejected",
                        "size_bytes": 512,
                        "error": {
                            "code": "UNSUPPORTED_FILE_TYPE",
                            "message": "Unsupported file extension .txt",
                            "details": {},
                        },
                    },
                ],
            }
        }
    )

    batch_id: UUID = Field(..., description="Correlation id for this batch.")
    collection_id: UUID = Field(..., description="Target collection UUID.")
    total: int = Field(..., ge=0, description="Number of files in the batch.")
    accepted: int = Field(..., ge=0, description="Files accepted as new tasks.")
    skipped: int = Field(
        ..., ge=0,
        description="Files skipped as duplicates (already ingested).",
    )
    rejected: int = Field(
        ..., ge=0,
        description="Files rejected by boundary validation.",
    )
    files: list[BatchFileResult] = Field(
        ..., description="Per-file results, in submission order.",
    )


__all__ = [
    "BatchFileResult",
    "BatchFileStatus",
    "BatchFileUpload",
    "BatchUploadResponse",
    "UploadPolicy",
]
