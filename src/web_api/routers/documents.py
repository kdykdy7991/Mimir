"""
``/documents/{id}`` detail/delete endpoints.

M2 batch 1: wired to ``DocumentService``. Document ids are stable UUIDs
derived from ``(collection, source_path)`` — see ``mappers.document_uuid``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from src.application.composition import ApplicationServices
from src.ingestion.storage.bm25_locks import bm25_write_lock
from src.web_api.dependencies import get_application_services
from src.web_api.errors import DocumentDeleteError, DocumentNotFoundError
from src.web_api.mappers import resolve_document, to_document_detail
from src.web_api.schemas.documents import DocumentDetail

router = APIRouter(prefix="/documents", tags=["documents"])


def _parse_document_id(document_id: str) -> UUID:
    try:
        return UUID(document_id)
    except ValueError as exc:
        raise DocumentNotFoundError(
            f"document {document_id!r} does not exist",
            details={"document_id": document_id},
        ) from exc


@router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    summary="Get a single document",
)
async def get_document(
    document_id: str = Path(..., description="Document ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> DocumentDetail:
    """Fetch document detail by ID."""
    resolved = resolve_document(services, _parse_document_id(document_id))
    if resolved is None:
        raise DocumentNotFoundError(
            f"document {document_id!r} does not exist",
            details={"document_id": document_id},
        )
    collection, source_path = resolved
    detail = services.document.get_document_detail(source_path, collection)
    if detail is None:
        raise DocumentNotFoundError(
            f"document {document_id!r} does not exist",
            details={"document_id": document_id},
        )
    return to_document_detail(detail.info, services=services)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document (coordinated)",
)
async def delete_document(
    document_id: str = Path(..., description="Document ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> None:
    """Coordinated delete across vector store + BM25 + image storage."""
    resolved = resolve_document(services, _parse_document_id(document_id))
    if resolved is None:
        raise DocumentNotFoundError(
            f"document {document_id!r} does not exist",
            details={"document_id": document_id},
        )
    collection, source_path = resolved
    # Delete + cache-invalidate run under the per-collection write lock,
    # serializing with concurrent uploads/deletes on the same collection.
    with bm25_write_lock(collection):
        result = services.document.delete_document(source_path, collection)
        if result.errors:
            raise DocumentDeleteError(
                "document deletion was only partially completed; retry is safe",
                details={
                    "document_id": document_id,
                    "errors": result.errors,
                    "chunks_deleted": result.chunks_deleted,
                    "bm25_removed": result.bm25_removed,
                },
            )
        # The delete rewrote the collection's on-disk BM25 index; drop the
        # cached engines so the sparse path reloads it (M3 consistency loop).
        invalidate = getattr(services.engines, "invalidate_collection", None)
        if callable(invalidate):
            invalidate(collection)
    return None


__all__ = ["router"]
