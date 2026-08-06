"""
DTOs for the collections (knowledge base) endpoints.

Mirrors the v2 API surface:
- ``GET    /collections``
- ``POST   /collections``
- ``GET    /collections/{id}``
- ``PATCH  /collections/{id}``
- ``DELETE /collections/{id}``
- ``GET    /collections/{id}/documents``     (lives here for cohesion)
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.web_api.schemas._types import UtcDatetime
from src.web_api.schemas.common import Page


class CollectionSummary(BaseModel):
    """Lightweight collection row for the list endpoint."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "b2c1f0e8-1234-5678-9abc-def012345678",
                "name": "company-handbook",
                "description": "Internal HR & policies.",
                "document_count": 12,
                "chunk_count": 1820,
                "created_at": "2026-07-15T03:11:08.012Z",
                "updated_at": "2026-07-31T08:25:42.001Z",
            }
        }
    )

    id: UUID
    name: str = Field(..., description="Human-readable collection name.")
    description: str | None = Field(
        None, description="Optional long-form description.",
    )
    document_count: int = Field(
        0, description="Number of documents currently indexed.",
    )
    chunk_count: int = Field(
        0, description="Total chunks indexed across all documents.",
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime


class CollectionCreateRequest(BaseModel):
    """``POST /collections`` body."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "company-handbook",
                "description": "Internal HR & policies.",
            }
        }
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Human-readable collection name. Must be unique.",
    )
    description: str | None = Field(
        None, max_length=1024, description="Optional description.",
    )


class CollectionUpdateRequest(BaseModel):
    """``PATCH /collections/{id}`` body."""

    description: str | None = Field(
        ..., max_length=1024, description="Updated description; null clears it.",
    )


class CollectionDetail(CollectionSummary):
    """``GET /collections/{id}`` response — same as summary for now.

    Kept as a separate type so future fields (e.g. configuration overrides)
    can be added without breaking the list response.
    """


class CollectionListResponse(Page[CollectionSummary]):
    """``GET /collections`` response — page of summaries.

    Example (empty list)::

        {
          "items": [],
          "page_info": {"next_cursor": null, "has_more": false}
        }
    """


__all__ = [
    "CollectionSummary",
    "CollectionCreateRequest",
    "CollectionUpdateRequest",
    "CollectionDetail",
    "CollectionListResponse",
]
