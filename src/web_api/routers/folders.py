"""
Collection-scoped document folders (task book B2.4).

Endpoints:

- ``GET    /collections/{collection_id}/folders``
- ``POST   /collections/{collection_id}/folders``
- ``PATCH  /collections/{collection_id}/folders/{folder_id}``  (rename)
- ``POST   /collections/{collection_id}/folders/{folder_id}/move``  (reparent)
- ``DELETE /collections/{collection_id}/folders/{folder_id}``
- ``PUT    /documents/{document_id}/folder``  (set/clear document folder)

Folders are logical directories only — original files never move. A folder
that does not exist in the requested collection resolves to 404 so
cross-collection existence is not leaked.
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
    FolderNotFoundError,
)
from src.web_api.mappers import collection_uuid, resolve_collection_name, resolve_document
from src.web_api.schemas.folders import (
    DocumentFolderResponse,
    DocumentFolderUpdateRequest,
    Folder,
    FolderCreateRequest,
    FolderListResponse,
    FolderMoveRequest,
    FolderRenameRequest,
)

router = APIRouter(tags=["folders"])


def _ts(value: float) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _to_folder(row: dict, collection_id: UUID, document_count: int) -> Folder:
    return Folder(
        id=row["folder_id"],
        collection_id=collection_id,
        parent_id=row["parent_id"],
        name=row["name"],
        depth=int(row["depth"]),
        document_count=document_count,
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


def _folder_in_collection(db, folder_id: str, collection_id: UUID) -> dict | None:
    row = db.get_folder(folder_id)
    if row is None or row["collection_id"] != str(collection_id):
        return None
    return row


@router.get(
    "/collections/{collection_id}/folders",
    response_model=FolderListResponse,
    summary="List a collection's folders",
)
async def list_folders(
    collection_id: UUID = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> FolderListResponse:
    _resolve_collection(services, collection_id)
    rows = services.db.list_folders(str(collection_id))
    items = [
        _to_folder(row, collection_id, services.db.count_documents_in_folder(row["folder_id"]))
        for row in rows
    ]
    return FolderListResponse(items=items)


@router.post(
    "/collections/{collection_id}/folders",
    response_model=Folder,
    status_code=status.HTTP_201_CREATED,
    summary="Create a folder",
)
async def create_folder(
    body: FolderCreateRequest,
    collection_id: UUID = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> Folder:
    _resolve_collection(services, collection_id)
    try:
        row = services.db.create_folder(
            collection_id=str(collection_id),
            name=body.name,
            parent_id=body.parent_id,
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise ConflictError("a folder with this name already exists here") from exc
    return _to_folder(row, collection_id, 0)


@router.patch(
    "/collections/{collection_id}/folders/{folder_id}",
    response_model=Folder,
    summary="Rename a folder",
)
async def rename_folder(
    body: FolderRenameRequest,
    collection_id: UUID = Path(...),
    folder_id: str = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> Folder:
    _resolve_collection(services, collection_id)
    if _folder_in_collection(services.db, folder_id, collection_id) is None:
        raise FolderNotFoundError(f"folder {folder_id!r} does not exist", details={"folder_id": folder_id})
    try:
        row = services.db.rename_folder(folder_id, body.name)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise ConflictError("a folder with this name already exists here") from exc
    return _to_folder(row, collection_id, services.db.count_documents_in_folder(folder_id))


@router.post(
    "/collections/{collection_id}/folders/{folder_id}/move",
    response_model=Folder,
    summary="Move (re-parent) a folder",
)
async def move_folder(
    body: FolderMoveRequest,
    collection_id: UUID = Path(...),
    folder_id: str = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> Folder:
    _resolve_collection(services, collection_id)
    if _folder_in_collection(services.db, folder_id, collection_id) is None:
        raise FolderNotFoundError(f"folder {folder_id!r} does not exist", details={"folder_id": folder_id})
    try:
        row = services.db.move_folder(folder_id=folder_id, new_parent_id=body.parent_id)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return _to_folder(row, collection_id, services.db.count_documents_in_folder(folder_id))


@router.delete(
    "/collections/{collection_id}/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a folder (children and documents move up to its parent)",
)
async def delete_folder(
    collection_id: UUID = Path(...),
    folder_id: str = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> None:
    _resolve_collection(services, collection_id)
    if _folder_in_collection(services.db, folder_id, collection_id) is None:
        raise FolderNotFoundError(f"folder {folder_id!r} does not exist", details={"folder_id": folder_id})
    services.db.delete_folder(folder_id)
    return None


@router.put(
    "/documents/{document_id}/folder",
    response_model=DocumentFolderResponse,
    summary="Set or clear a document's folder placement",
)
async def set_document_folder(
    body: DocumentFolderUpdateRequest,
    document_id: UUID = Path(..., description="Document ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> DocumentFolderResponse:
    resolved = resolve_document(services, document_id)
    if resolved is None:
        raise DocumentNotFoundError(
            f"document {document_id!r} does not exist",
            details={"document_id": str(document_id)},
        )
    collection_name, _source_path = resolved
    doc_collection_uuid = collection_uuid(collection_name)
    if body.folder_id is not None:
        if _folder_in_collection(services.db, body.folder_id, doc_collection_uuid) is None:
            raise BadRequestError(
                "folder does not exist in the document's collection",
                details={"folder_id": body.folder_id},
            )
    services.db.move_document(
        document_id=str(document_id), folder_id=body.folder_id,
        collection_id=str(doc_collection_uuid),
    )
    return DocumentFolderResponse(folder_id=body.folder_id)


__all__ = ["router"]