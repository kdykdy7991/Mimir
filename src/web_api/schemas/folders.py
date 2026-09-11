"""DTOs for collection-scoped document folders (task book B2.4)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.web_api.schemas._types import UtcDatetime


class Folder(BaseModel):
    """A logical document folder (a pure directory; files never move)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "9f1a2b3c-44aa-4b0c-8d1e-1a2b3c4d5e6f",
                "collection_id": "b2c1f0e8-1234-5678-9abc-def012345678",
                "parent_id": None,
                "name": "研发",
                "depth": 0,
                "document_count": 12,
                "created_at": "2026-09-11T08:00:00.000Z",
                "updated_at": "2026-09-11T08:00:00.000Z",
            }
        }
    )

    id: str = Field(..., description="Stable folder id (UUID string).")
    collection_id: UUID = Field(..., description="Owning collection id.")
    parent_id: str | None = Field(None, description="Parent folder id; null = root.")
    name: str = Field(..., min_length=1, max_length=64)
    depth: int = Field(..., ge=0, description="Distance from the collection root (0 = root).")
    document_count: int = Field(0, ge=0, description="Documents directly placed in this folder.")
    created_at: UtcDatetime
    updated_at: UtcDatetime


class FolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    parent_id: str | None = Field(None, description="Parent folder id; null = root.")


class FolderRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class FolderMoveRequest(BaseModel):
    parent_id: str | None = Field(
        None, description="New parent folder id; null = move to collection root.",
    )


class FolderListResponse(BaseModel):
    items: list[Folder]


class DocumentFolderUpdateRequest(BaseModel):
    folder_id: str | None = Field(
        None, description="Folder id to place the document in; null = move to root.",
    )


class DocumentFolderResponse(BaseModel):
    folder_id: str | None


__all__ = [
    "DocumentFolderResponse",
    "DocumentFolderUpdateRequest",
    "Folder",
    "FolderCreateRequest",
    "FolderListResponse",
    "FolderMoveRequest",
    "FolderRenameRequest",
]