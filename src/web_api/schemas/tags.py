"""DTOs for collection-scoped document tags (task book B2.2)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.web_api.schemas._types import UtcDatetime


class Tag(BaseModel):
    """A collection-scoped tag."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "3c2f1b0a-99aa-4422-8811-aabbccddeeff",
                "collection_id": "b2c1f0e8-1234-5678-9abc-def012345678",
                "name": "财务",
                "color": "blue",
                "created_at": "2026-09-11T08:00:00.000Z",
                "updated_at": "2026-09-11T08:00:00.000Z",
            }
        }
    )

    id: str = Field(..., description="Stable tag id (UUID string).")
    collection_id: UUID = Field(..., description="Owning collection id.")
    name: str = Field(..., min_length=1, max_length=64)
    color: str = Field("grey", description="Controlled colour token.")
    created_at: UtcDatetime
    updated_at: UtcDatetime


class TagCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    color: str = Field("grey", description="One of the allowed colour tokens.")


class TagUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    color: str | None = Field(None, description="One of the allowed colour tokens.")


class TagListResponse(BaseModel):
    items: list[Tag]


class DocumentTagsUpdateRequest(BaseModel):
    """Full-replace body for ``PUT /documents/{doc}/tags``."""

    tag_ids: list[str] = Field(..., description="Complete tag id set for the document.")


class DocumentTagsResponse(BaseModel):
    """Effective tag set after a full-replace binding."""

    tag_ids: list[str]


__all__ = [
    "DocumentTagsResponse",
    "DocumentTagsUpdateRequest",
    "Tag",
    "TagCreateRequest",
    "TagListResponse",
    "TagUpdateRequest",
]