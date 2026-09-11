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

SourceLocatorKind = Literal["pdf_page", "image", "section", "none"]
"""Normalized source-position discriminators (task book B1.3).

``none`` means there is no reliable locator — the API must not guess.
"""


class SourceLocator(BaseModel):
    """Where, in the original file, a chunk points to (task book B1.3).

    ``pdf_page`` carries a 1-based ``page``; ``image``/``section``/``none``
    have no page (``page`` is ``None``). The preview URL stays behind the
    existing controlled preview endpoint — no filesystem path leaks here.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"kind": "pdf_page", "page": 12},
        }
    )

    kind: SourceLocatorKind = Field(
        ..., description="Normalized locator kind; never guessed.",
    )
    page: int | None = Field(
        None, ge=1,
        description="1-based page, set only when ``kind == 'pdf_page'``.",
    )


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


class DocumentChunkSummary(BaseModel):
    """Lightweight structural row for the document-detail chunk table."""

    index: int = Field(..., ge=0)
    chunk_id: str
    heading: str | None = None
    page: int | None = None
    character_count: int = Field(..., ge=0)
    content_type: str = "text"


class DocumentChunkDetail(BaseModel):
    """``GET /documents/{document_id}/chunks/{chunk_id}`` response (B1.1).

    Full chunk text plus stable navigation. Missing page/heading/neighbors
    serialise as ``null`` rather than erroring, so old or sparse data stays
    renderable.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "chunk_id": "stable-id",
                "document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                "index": 17,
                "text": "3.2 服务部署 ……",
                "heading": "3.2 服务部署",
                "page": 12,
                "content_type": "text",
                "character_count": 864,
                "previous_chunk_id": "...",
                "next_chunk_id": "...",
                "source_locator": {"kind": "pdf_page", "page": 12},
            }
        }
    )

    chunk_id: str = Field(..., description="Stable chunk identifier.")
    document_id: UUID = Field(..., description="Owning stable document ID.")
    index: int = Field(
        ..., ge=0,
        description="Zero-based position in the document's stable chunk order.",
    )
    text: str = Field(..., description="Full chunk body text.")
    heading: str | None = Field(None, description="Section heading if derivable.")
    page: int | None = Field(None, ge=1, description="Source page if derivable.")
    content_type: str = Field("text", description="Chunk type ('text'|'table'|...).")
    character_count: int = Field(..., ge=0, description="``len(text)``.")
    previous_chunk_id: str | None = Field(
        None, description="Previous chunk in the stable order, or null.",
    )
    next_chunk_id: str | None = Field(
        None, description="Next chunk in the stable order, or null.",
    )
    source_locator: SourceLocator = Field(
        ..., description="Normalized position in the original file.",
    )


class ChunkListItem(BaseModel):
    """Preview row for the server-paged chunk list (B1.2).

    Full text is intentionally not included — the client requests a single
    chunk via ``GET /documents/{id}/chunks/{id}`` for the drawer.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "index": 17,
                "chunk_id": "stable-id",
                "heading": "3.2 服务部署",
                "page": 12,
                "content_type": "text",
                "character_count": 864,
                "text_preview": "3.2 服务部署 ……",
            }
        }
    )

    index: int = Field(..., ge=0)
    chunk_id: str
    heading: str | None = None
    page: int | None = Field(None, ge=1)
    content_type: str = "text"
    character_count: int = Field(..., ge=0)
    text_preview: str = Field(..., description="Whitespace-normalized snippet.")


class DocumentChunkListResponse(BaseModel):
    """``GET /documents/{id}/chunks`` response (B1.2)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [],
                "page": 1,
                "page_size": 50,
                "total": 0,
                "has_next": False,
            }
        }
    )

    items: list[ChunkListItem] = Field(..., description="Current page rows.")
    page: int = Field(..., ge=1, description="Requested 1-based page.")
    page_size: int = Field(..., ge=1, description="Requested page size.")
    total: int = Field(..., ge=0, description="Total rows after filtering.")
    has_next: bool = Field(
        ..., description="True when another page exists after this one.",
    )


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
    table_count: int = Field(
        0,
        description="Number of distinct table blocks recorded by table-aware chunking.",
    )
    parse_warnings: list[str] = Field(
        default_factory=list,
        description="Parser-emitted partial-success warnings; empty when none were emitted.",
    )
    parser_engine: str | None = Field(
        None,
        description="Parser engine recorded in persisted chunk metadata.",
    )
    parse_status: str | None = Field(
        None,
        description="Parser status recorded in persisted chunk metadata.",
    )
    page_count: int | None = Field(
        None, ge=0,
        description="Source page count when supplied by the parser.",
    )
    vision_processed: bool | None = Field(
        None,
        description="Whether persisted evidence shows that visual processing ran.",
    )
    chunks: list[DocumentChunkSummary] = Field(
        default_factory=list,
        description="Ordered structural chunk rows without embedding vectors.",
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
    "SourceLocator",
    "SourceLocatorKind",
    "DocumentSummary",
    "DocumentChunkSummary",
    "DocumentChunkDetail",
    "ChunkListItem",
    "DocumentChunkListResponse",
    "DocumentDetail",
    "DocumentListResponse",
    "DocumentUploadResponse",
]
