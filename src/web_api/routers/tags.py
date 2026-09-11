"""
Collection-scoped document tags (task book B2.2).

Endpoints:

- ``GET    /collections/{collection_id}/tags``
- ``POST   /collections/{collection_id}/tags``
- ``PATCH  /collections/{collection_id}/tags/{tag_id}``
- ``DELETE /collections/{collection_id}/tags/{tag_id}``
- ``PUT    /documents/{document_id}/tags`` — full-replace document binding

All tag access is collection-scoped: a tag that does not exist *in the given
collection* resolves to 404, so cross-collection resource existence is not
leaked.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from src.application.composition import ApplicationServices
from src.web_api.dependencies import get_application_services
from src.web_api.errors import (
    BadRequestError,
    CollectionNotFoundError,
    ConflictError,
    DocumentNotFoundError,
    TagNotFoundError,
)
from src.web_api.mappers import collection_uuid, resolve_collection_name, resolve_document
from src.web_api.schemas.tags import (
    DocumentTagsResponse,
    DocumentTagsUpdateRequest,
    Tag,
    TagCreateRequest,
    TagListResponse,
    TagUpdateRequest,
)

router = APIRouter(tags=["tags"])


def _ts(value: float) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _to_tag(row: dict, collection_id: UUID) -> Tag:
    return Tag(
        id=row["tag_id"],
        collection_id=collection_id,
        name=row["name"],
        color=row["color"],
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
    )


def _resolve_collection(services: ApplicationServices, collection_id: UUID) -> str:
    name = resolve_collection_name(services, collection_id)
    if name is None:
        raise CollectionNotFoundError(
            f"collection {collection_id} does not exist",
            details={"collection_id": str(collection_id)},
        )
    return name


def _tag_in_collection(db, tag_id: str, collection_id: UUID) -> dict | None:
    """Return the tag only when it belongs to ``collection_id`` (else None)."""
    row = db.get_tag(tag_id)
    if row is None or row["collection_id"] != str(collection_id):
        return None
    return row


# ---------------------------------------------------------------------------
# Tag CRUD (collection-scoped)
# ---------------------------------------------------------------------------

@router.get(
    "/collections/{collection_id}/tags",
    response_model=TagListResponse,
    summary="List a collection's tags",
)
async def list_tags(
    collection_id: UUID = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> TagListResponse:
    _resolve_collection(services, collection_id)
    rows = services.db.list_tags(str(collection_id))
    return TagListResponse(
        items=[_to_tag(row, collection_id) for row in rows],
    )


@router.post(
    "/collections/{collection_id}/tags",
    response_model=Tag,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tag in a collection",
)
async def create_tag(
    body: TagCreateRequest,
    collection_id: UUID = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> Tag:
    _resolve_collection(services, collection_id)
    try:
        row = services.db.create_tag(
            collection_id=str(collection_id),
            name=body.name,
            color=body.color or "grey",
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise ConflictError("a tag with this name already exists in the collection") from exc
    return _to_tag(row, collection_id)


@router.patch(
    "/collections/{collection_id}/tags/{tag_id}",
    response_model=Tag,
    summary="Rename or recolour a tag",
)
async def update_tag(
    body: TagUpdateRequest,
    collection_id: UUID = Path(...),
    tag_id: str = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> Tag:
    _resolve_collection(services, collection_id)
    if _tag_in_collection(services.db, tag_id, collection_id) is None:
        raise TagNotFoundError(f"tag {tag_id!r} does not exist", details={"tag_id": tag_id})
    try:
        row = services.db.update_tag(
            tag_id=tag_id, name=body.name, color=body.color,
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise ConflictError("a tag with this name already exists in the collection") from exc
    return _to_tag(row, collection_id)


@router.delete(
    "/collections/{collection_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tag (links are cascaded, documents are kept)",
)
async def delete_tag(
    collection_id: UUID = Path(...),
    tag_id: str = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> None:
    _resolve_collection(services, collection_id)
    if _tag_in_collection(services.db, tag_id, collection_id) is None:
        raise TagNotFoundError(f"tag {tag_id!r} does not exist", details={"tag_id": tag_id})
    services.db.delete_tag(tag_id)
    return None


# ---------------------------------------------------------------------------
# Document tag binding (full replace, transactional)
# ---------------------------------------------------------------------------

@router.put(
    "/documents/{document_id}/tags",
    response_model=DocumentTagsResponse,
    summary="Full-replace the tags assigned to a document",
)
async def set_document_tags(
    body: DocumentTagsUpdateRequest,
    document_id: UUID = Path(..., description="Document ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> DocumentTagsResponse:
    """Bind a document to exactly the given tags (transactional replace).

    Only tags belonging to the document's own collection may be assigned;
    any unknown or cross-collection tag id makes the whole request fail
    (400) without partial writes. Duplicate ids are deduplicated.
    """
    resolved = resolve_document(services, document_id)
    if resolved is None:
        raise DocumentNotFoundError(
            f"document {document_id!r} does not exist",
            details={"document_id": str(document_id)},
        )
    collection_name, _source_path = resolved
    doc_collection_uuid = collection_uuid(collection_name)

    deduped = list(dict.fromkeys(body.tag_ids))
    for tag_id in deduped:
        if _tag_in_collection(services.db, tag_id, doc_collection_uuid) is None:
            raise BadRequestError(
                "one or more tag ids do not exist in the document's collection",
                details={"tag_id": tag_id},
            )
    services.db.set_document_tags(str(document_id), deduped)
    return DocumentTagsResponse(tag_ids=deduped)


__all__ = ["router"]