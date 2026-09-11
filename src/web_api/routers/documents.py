"""
``/documents/{id}`` detail/delete endpoints.

M2 batch 1: wired to ``DocumentService``. Document ids are stable UUIDs
derived from ``(collection, source_path)`` — see ``mappers.document_uuid``.
"""

from __future__ import annotations

import re
from pathlib import Path as FilePath
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import FileResponse

from src.application.composition import ApplicationServices
from src.ingestion.chunk_order import (
    build_source_locator,
    chunk_id_of,
    heading_of,
    page_number_of,
    stable_order_chunks,
)
from src.ingestion.storage.bm25_locks import bm25_write_lock
from src.web_api.dependencies import get_application_services
from src.web_api.errors import (
    BadRequestError,
    ChunkNotFoundError,
    DocumentDeleteError,
    DocumentNotFoundError,
)
from src.web_api.mappers import (
    build_cursor_page_info,
    decode_offset_cursor,
    resolve_document,
    to_document_detail,
    to_document_summary,
)
from src.web_api.schemas.documents import (
    ChunkListItem,
    DocumentChunkDetail,
    DocumentChunkListResponse,
    DocumentDetail,
    DocumentListResponse,
)
from src.web_api.settings import SETTINGS

router = APIRouter(prefix="/documents", tags=["documents"])

_PREVIEW_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".md": "text/markdown; charset=utf-8", ".markdown": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8",
    ".html": "text/plain; charset=utf-8", ".htm": "text/plain; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def _parse_document_id(document_id: str) -> UUID:
    try:
        return UUID(document_id)
    except ValueError as exc:
        raise DocumentNotFoundError(
            f"document {document_id!r} does not exist",
            details={"document_id": document_id},
        ) from exc


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List all documents across collections",
)
async def list_all_documents(
    services: ApplicationServices = Depends(get_application_services),
    cursor: str | None = Query(
        None, description="Opaque cursor from the previous page's ``next_cursor``.",
    ),
    limit: int = Query(
        SETTINGS.page_limit_default,
        ge=1,
        le=SETTINGS.page_limit_max,
        description=f"Page size, 1-{SETTINGS.page_limit_max}.",
    ),
) -> DocumentListResponse:
    """Server-side paginated listing of documents across **every** collection.

    Mirrors the per-collection paging path: the page is selected in
    SQLite (newest-first across the whole corpus) and only the current
    page's chunk / image counts are queried — no full-corpus materialisation.
    """
    offset = decode_offset_cursor(cursor)
    infos, total = services.document.list_documents_paged(
        offset=offset, limit=limit,
    )
    page = [to_document_summary(info) for info in infos]
    page_info = build_cursor_page_info(offset, len(page), total)
    return DocumentListResponse(items=page, page_info=page_info)


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
    return to_document_detail(detail.info, services=services, chunks=detail.chunks)


def _sanitize_page(value: int | None) -> int | None:
    """Page must be a positive 1-based int, else ``None`` (missing)."""
    if value is None or value < 1:
        return None
    return value


def _chunk_preview(text: str, limit: int = 200) -> str:
    """Whitespace-normalized preview snippet for list rows."""
    compact = re.sub(r"\s+", " ", text.strip())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"


@router.get(
    "/{document_id}/chunks",
    response_model=DocumentChunkListResponse,
    summary="Paginate, search, and filter a document's chunks",
)
async def list_document_chunks(
    document_id: str = Path(..., description="Document ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
    page: int = Query(1, ge=1, description="1-based page (min 1)."),
    page_size: int = Query(
        SETTINGS.chunk_page_size_default,
        ge=1,
        le=SETTINGS.chunk_page_size_max,
        description=f"Rows per page, 1-{SETTINGS.chunk_page_size_max}.",
    ),
    q: str | None = Query(None, description="Case-insensitive literal text search."),
    content_type: Literal["text", "table", "image_ocr", "image_caption"] | None =
        Query(None, description="Chunk content-type filter."),
    page_number: int | None = Query(None, ge=1, description="Filter by source page."),
) -> DocumentChunkListResponse:
    """Server-side paginated chunk list with literal search + filters.

    Ordering matches the MCP ``get_document_chunks`` tool (shared
    ``stable_order_chunks`` helper). ``q`` strips whitespace and is a
    case-insensitive literal substring match (no full-text engine). The
    ``page_number`` filter is *source page*, distinct from the ``page``
    pagination index. Rows are preview summaries — full text comes from
    ``GET /documents/{id}/chunks/{chunk_id}``.
    """
    query = (q or "").strip()
    if len(query) > 200:
        raise BadRequestError(
            "chunk search query is limited to 200 characters",
            details={"query_length": len(query), "max_length": 200},
        )

    doc_uuid = _parse_document_id(document_id)
    resolved = resolve_document(services, doc_uuid)
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

    ordered = stable_order_chunks(detail.chunks)
    query_lower = query.lower() if query else None
    matching: list[tuple[int, dict]] = []
    for idx, hit in enumerate(ordered):
        text = str(hit.get("text") or "")
        meta = hit.get("metadata") or {}
        if query_lower is not None and query_lower not in text.lower():
            continue
        chunk_type = str(meta.get("content_type") or "text")
        if content_type is not None and chunk_type != content_type:
            continue
        if page_number is not None and page_number_of(meta) != page_number:
            continue
        matching.append((idx, hit))

    total = len(matching)
    start = (page - 1) * page_size
    window = matching[start:start + page_size]
    items: list[ChunkListItem] = []
    for idx, hit in window:
        meta = hit.get("metadata") or {}
        text = str(hit.get("text") or "")
        chunk_type = str(meta.get("content_type") or "text")
        items.append(ChunkListItem(
            index=idx,
            chunk_id=chunk_id_of(hit),
            heading=heading_of(hit),
            page=_sanitize_page(page_number_of(meta)),
            content_type=chunk_type,
            character_count=len(text),
            text_preview=_chunk_preview(text),
        ))

    return DocumentChunkListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_next=start + len(window) < total,
    )


@router.get(
    "/{document_id}/chunks/{chunk_id}",
    response_model=DocumentChunkDetail,
    summary="Get an authorized chunk's full text and neighbors",
)
async def get_document_chunk(
    document_id: str = Path(..., description="Document ID (UUID)."),
    chunk_id: str = Path(..., description="Stable chunk identifier."),
    services: ApplicationServices = Depends(get_application_services),
) -> DocumentChunkDetail:
    """Return a single chunk's full body, source locator, and stable neighbors.

    Ownership is resolved by document first; a chunk id that does not belong
    to this document returns ``404`` without leaking why. Ordering (and hence
    ``previous_chunk_id`` / ``next_chunk_id``) reuses the MCP
    ``get_document_chunks`` stable order — the same helper, not a second rule.
    """
    doc_uuid = _parse_document_id(document_id)
    resolved = resolve_document(services, doc_uuid)
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

    ordered = stable_order_chunks(detail.chunks)
    pos = next(
        (i for i, hit in enumerate(ordered) if chunk_id_of(hit) == chunk_id),
        None,
    )
    if pos is None:
        raise ChunkNotFoundError(
            f"chunk {chunk_id!r} does not exist in document {document_id!r}",
            details={"chunk_id": chunk_id, "document_id": document_id},
        )

    hit = ordered[pos]
    text = str(hit.get("text") or "")
    meta = hit.get("metadata") or {}
    content_type = str(meta.get("content_type") or "text")
    prior = ordered[pos - 1] if pos > 0 else None
    nxt = ordered[pos + 1] if pos + 1 < len(ordered) else None
    heading = heading_of(hit)
    locator = build_source_locator(
        meta,
        source_path=source_path,
        content_type=content_type,
        heading=heading,
    )
    return DocumentChunkDetail(
        chunk_id=chunk_id,
        document_id=doc_uuid,
        index=pos,
        text=text,
        heading=heading,
        page=_sanitize_page(page_number_of(meta)),
        content_type=content_type,
        character_count=len(text),
        previous_chunk_id=chunk_id_of(prior) if prior is not None else None,
        next_chunk_id=chunk_id_of(nxt) if nxt is not None else None,
        source_locator=locator,
    )


@router.get("/{document_id}/preview", summary="Preview the original uploaded document")
async def preview_document(
    document_id: str = Path(..., description="Document ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> FileResponse:
    """Stream only persisted Web uploads; never expose arbitrary CLI paths."""
    resolved = resolve_document(services, _parse_document_id(document_id))
    if resolved is None:
        raise DocumentNotFoundError(f"document {document_id!r} does not exist")
    _collection, source_path = resolved
    candidate = FilePath(source_path).resolve()
    upload_root = services.ingestion.upload_dir.resolve()
    if not candidate.is_relative_to(upload_root) or not candidate.is_file():
        raise DocumentNotFoundError(
            "original upload is unavailable; re-upload the document to enable preview",
            details={"document_id": document_id, "preview_available": False},
        )
    return FileResponse(
        candidate,
        media_type=_PREVIEW_MEDIA_TYPES.get(candidate.suffix.lower(), "application/octet-stream"),
        filename=candidate.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


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
