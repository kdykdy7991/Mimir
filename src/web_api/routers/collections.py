"""
``/collections`` + ``/collections/{id}/documents`` endpoints.

M2 batch 1: list / create / get / delete wired to ``DocumentService``.
M2 batch 2: single upload is wired to ``IngestionService.upload`` — the
endpoint launches the pipeline in a background thread. The HTTP response
returns immediately with a 202 + ``DocumentUploadResponse`` carrying the
``task_id``; clients poll ``GET /tasks/{id}`` for status. M5: the bytes
are staged to a **per-task temp path**
(``data/uploads/<collection>/.tmp/<task_id>-<filename>``) and the worker
ingests from it while keeping the stable canonical document identity
(``data/uploads/<collection>/<sanitised_filename>`` is never written), so
concurrent same-name uploads cannot overwrite each other.
M5: the same endpoint also accepts ``files[]`` for batch upload —
``IngestionService.upload_batch`` validates each file independently
(extension + MIME + size + duplicate name + dedup pre-check) and returns
a ``BatchUploadResponse`` with a ``batch_id`` and per-file results.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Path, Query, UploadFile, status

from src.application.composition import ApplicationServices
from src.application.services import BatchFileUpload
from src.ingestion.storage.bm25_locks import bm25_write_lock
from src.web_api.dependencies import get_application_services
from src.web_api.errors import (
    BadRequestError,
    CollectionNotFoundError,
    DuplicateCollectionError,
    FolderNotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from src.web_api.mappers import (
    build_cursor_page_info,
    decode_offset_cursor,
    document_uuid,
    paginate,
    resolve_collection_name,
    to_collection_detail,
    to_collection_summary,
    to_document_summary,
)
from src.web_api.schemas.collections import (
    CollectionCreateRequest,
    CollectionDetail,
    CollectionListResponse,
    CollectionUpdateRequest,
)
from src.web_api.schemas.documents import (
    BatchUploadResponse,
    DocumentDetail,
    DocumentListResponse,
    DocumentTagRef,
    DocumentUploadResponse,
)
from src.web_api.settings import SETTINGS

router = APIRouter(prefix="/collections", tags=["collections"])


def _description(services: ApplicationServices, name: str) -> str | None:
    db = getattr(services, "db", None)
    if db is None:
        return None
    row = db.get_collection(name)
    return row.get("description") if row is not None else None


@router.get(
    "",
    response_model=CollectionListResponse,
    summary="List collections",
)
async def list_collections(
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
) -> CollectionListResponse:
    """Cursor-paginated list of collections."""
    refs = services.document.list_collections()
    # Page the *references* first so stats are computed only for the
    # collections on this page, not the whole corpus.
    page_refs, page_info = paginate(refs, cursor, limit)
    names = [ref.name for ref in page_refs]
    stats = services.document.get_all_collection_stats_cached(names)
    summaries = [
        to_collection_summary(
            ref, stats[ref.name],
            description=_description(services, ref.name),
        )
        for ref in page_refs
    ]
    return CollectionListResponse(items=summaries, page_info=page_info)


@router.post(
    "",
    response_model=CollectionDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a collection",
)
async def create_collection(
    body: CollectionCreateRequest,
    services: ApplicationServices = Depends(get_application_services),
) -> CollectionDetail:
    """Create a new collection (persists a BM25 index marker)."""
    existing = services.document.list_collections()
    if any(ref.name == body.name for ref in existing):
        raise DuplicateCollectionError(
            f"collection {body.name!r} already exists",
            details={"collection_name": body.name},
        )
    services.document.create_collection(body.name)
    # stats cache invalidation handled inside DocumentService.create_collection
    db = getattr(services, "db", None)
    if db is not None:
        db.upsert_collection(body.name, body.description)
    ref = next(
        r for r in services.document.list_collections() if r.name == body.name
    )
    stats = services.document.get_collection_stats(collection=body.name)
    return to_collection_detail(
        ref, stats, description=_description(services, body.name),
    )


@router.get(
    "/{collection_id}",
    response_model=CollectionDetail,
    summary="Get a single collection",
)
async def get_collection(
    collection_id: UUID = Path(..., description="Collection ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> CollectionDetail:
    """Fetch a single collection by ID."""
    name = resolve_collection_name(services, collection_id)
    if name is None:
        raise CollectionNotFoundError(
            f"collection {collection_id} does not exist",
            details={"collection_id": str(collection_id)},
        )
    ref = next(r for r in services.document.list_collections() if r.name == name)
    stats = services.document.get_collection_stats(collection=name)
    return to_collection_detail(
        ref, stats, description=_description(services, name),
    )


@router.patch(
    "/{collection_id}",
    response_model=CollectionDetail,
    summary="Update a collection",
)
async def update_collection(
    body: CollectionUpdateRequest,
    collection_id: UUID = Path(..., description="Collection ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> CollectionDetail:
    """Update mutable knowledge-base metadata."""
    name = resolve_collection_name(services, collection_id)
    if name is None:
        raise CollectionNotFoundError(
            f"collection {collection_id} does not exist",
            details={"collection_id": str(collection_id)},
        )
    db = getattr(services, "db", None)
    if db is None:
        raise BadRequestError(
            "Collection metadata storage is unavailable",
            details={"collection_id": str(collection_id)},
        )
    db.upsert_collection(name, body.description)
    ref = next(r for r in services.document.list_collections() if r.name == name)
    stats = services.document.get_collection_stats(collection=name)
    return to_collection_detail(
        ref, stats, description=_description(services, name),
    )


@router.delete(
    "/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a collection",
)
async def delete_collection(
    collection_id: UUID = Path(..., description="Collection ID (UUID)."),
    services: ApplicationServices = Depends(get_application_services),
) -> None:
    """Delete a collection and all of its documents."""
    name = resolve_collection_name(services, collection_id)
    if name is None:
        raise CollectionNotFoundError(
            f"collection {collection_id} does not exist",
            details={"collection_id": str(collection_id)},
        )
    with bm25_write_lock(name):
        services.document.delete_collection(name)
        # Cascade delete rewrote/removed the collection's BM25 index;
        # drop the cached engines so a later re-create starts fresh
        # (M3 loop). Serialized with concurrent writers per collection.
        invalidate = getattr(services.engines, "invalidate_collection", None)
        if callable(invalidate):
            invalidate(name)
        db = getattr(services, "db", None)
        if db is not None:
            db.delete_collection(name)
    # stats cache invalidation handled inside DocumentService.delete_collection
    return None


_VALID_SORTS = frozenset({
    "updated_desc", "updated_asc", "name_asc", "name_desc", "size_desc",
})
# Contract status → integrity storage status (success/failed ⇄ ready/failed).
_INTEGRITY_STATUS = {"ready": "success", "failed": "failed"}


def _parse_ts_query(value: str | None, *, field: str) -> float | None:
    """RFC-3339 timestamp → epoch seconds; ``400`` on invalid input."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BadRequestError(
            f"invalid {field}: {value!r} (expected RFC-3339 timestamp)",
            details={"field": field, "value": value},
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _doc_ids_to_source_paths(
    services, collection_name: str, document_ids: list[str],
) -> list[str]:
    """Map WebApiDB document-UUIDs → source_paths within ``collection_name``."""
    if not document_ids:
        return []
    resolve = getattr(services.document, "resolve_document_id", None)
    if not callable(resolve):
        return []
    out: list[str] = []
    for did in document_ids:
        try:
            key = UUID(did)
        except ValueError:
            continue
        resolved = resolve(key)
        if resolved is not None and resolved[0] == collection_name:
            out.append(resolved[1])
    return out


def _enrich_summaries(
    services, infos, summaries, *, collection_id: str,
) -> None:
    """Fill each summary's ``tags`` / ``folder_id`` from the WebApiDB (B2.5)."""
    db = getattr(services, "db", None)
    if db is None or not infos or not summaries:
        return
    doc_ids = [
        str(document_uuid(info.collection, info.source_path)) for info in infos
    ]
    tags_by_doc = db.document_tags_map(doc_ids)
    folder_by_doc = db.document_folder_map(doc_ids, collection_id)
    tag_lookup = {t["tag_id"]: t for t in db.list_tags(collection_id)}
    for summary, did in zip(summaries, doc_ids):
        tags: list[DocumentTagRef] = []
        for tid in tags_by_doc.get(did, []):
            row = tag_lookup.get(tid)
            if row is not None:
                tags.append(DocumentTagRef(
                    id=UUID(row["tag_id"]),
                    name=row["name"],
                    color=row["color"],
                ))
        summary.tags = tags
        summary.folder_id = folder_by_doc.get(did)


@router.get(
    "/{collection_id}/documents",
    response_model=DocumentListResponse,
    summary="List documents in a collection",
)
async def list_collection_documents(
    collection_id: UUID = Path(..., description="Collection ID (UUID)."),
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
    q: str | None = Query(
        None, description="Case-insensitive substring on filename/path.",
    ),
    folder_id: str | None = Query(
        None, description="A folder UUID, or literal ``root`` = collection root.",
    ),
    tag_id: list[str] | None = Query(
        None, description="Repeatable tag id; documents must carry ALL given tags.",
    ),
    status: str | None = Query(
        None, description="'ready' or 'failed'.",
    ),
    file_type: str | None = Query(
        None, description="File extension without a leading dot.",
    ),
    updated_after: str | None = Query(
        None, description="RFC-3339 timestamp; documents updated at/after this.",
    ),
    updated_before: str | None = Query(
        None, description="RFC-3339 timestamp; documents updated at/before this.",
    ),
    sort: str | None = Query(
        "updated_desc",
        description="updated_desc | updated_asc | name_asc | name_desc | size_desc.",
    ),
) -> DocumentListResponse:
    """Cursor-paginated list of documents belonging to a collection.

    No longer loads every document + chunk + image before paging: the
    current page is selected in SQLite (``LIMIT/OFFSET``), then only that
    page's chunk / image counts are queried.

    B2.5: ``q`` / ``status`` / ``file_type`` / ``updated_after`` /
    ``updated_before`` / ``sort`` and the folder / tag document-UUID sets
    (mapped back to ``source_paths``) are pushed into the integrity SQL as
    ``WHERE`` predicates — the store never returns the whole collection for
    Python-side filtering. Every page item is enriched with ``tags`` and
    ``folder_id`` from the ``WebApiDB``.
    """
    name = resolve_collection_name(services, collection_id)
    if name is None:
        raise CollectionNotFoundError(
            f"collection {collection_id} does not exist",
            details={"collection_id": str(collection_id)},
        )
    if sort not in _VALID_SORTS:
        raise BadRequestError(
            f"invalid sort: {sort!r}",
            details={"field": "sort", "allowed": sorted(_VALID_SORTS)},
        )
    after = _parse_ts_query(updated_after, field="updated_after")
    before = _parse_ts_query(updated_before, field="updated_before")

    collection_scope = str(collection_id)
    db = getattr(services, "db", None)
    source_paths_include: list[str] | None = None

    # Folder filter (a folder UUID, or literal "root").
    if folder_id is not None:
        if db is None:
            source_paths_include = []
        elif folder_id == "root":
            source_paths_include = _doc_ids_to_source_paths(
                services, name,
                db.documents_by_folder(None, collection_scope),
            )
        else:
            folder = db.get_folder(folder_id)
            if folder is None or folder.get("collection_id") != collection_scope:
                raise FolderNotFoundError(
                    f"folder {folder_id} does not exist in this collection",
                    details={"folder_id": folder_id},
                )
            source_paths_include = _doc_ids_to_source_paths(
                services, name,
                db.documents_by_folder(folder_id, collection_scope),
            )

    # Multi-tag AND filter: documents must hold every requested tag.
    tag_ids = [t for t in (tag_id or []) if t]
    if tag_ids and db is not None:
        tagged_paths = _doc_ids_to_source_paths(
            services, name, db.documents_with_all_tags(tag_ids),
        )
        if source_paths_include is None:
            source_paths_include = tagged_paths
        else:
            # Intersect folder set ∩ tag set.
            source_paths_include = [
                p for p in source_paths_include if p in tagged_paths
            ]

    status_integrity = _INTEGRITY_STATUS.get(status) if status else None
    ft = file_type.lstrip(".") if file_type else None

    offset = decode_offset_cursor(cursor)
    infos, total = services.document.list_documents_paged(
        collection=name, offset=offset, limit=limit,
        status=status_integrity,
        q=q or None, file_type=ft,
        updated_after=after, updated_before=before,
        sort=sort, source_paths_include=source_paths_include,
    )
    page = [to_document_summary(info) for info in infos]
    _enrich_summaries(services, infos, page, collection_id=collection_scope)
    page_info = build_cursor_page_info(offset, len(page), total)
    return DocumentListResponse(items=page, page_info=page_info)


@router.post(
    "/{collection_id}/documents",
    response_model=DocumentUploadResponse | BatchUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload documents and start ingestion",
)
async def upload_collection_document(
    background_tasks: BackgroundTasks,
    collection_id: UUID = Path(..., description="Collection ID (UUID)."),
    file: UploadFile | None = File(
        None, description="Single document file (PDF / Markdown).",
    ),
    files: list[UploadFile] | None = File(
        None, description="Batch of documents; all go to this collection.",
    ),
    services: ApplicationServices = Depends(get_application_services),
) -> DocumentUploadResponse | BatchUploadResponse:
    """Upload one or more documents to a collection and start ingestion.

    Two mutually-exclusive modes on the same endpoint:

    - **Single** (legacy, M2): send ``file``. Returns ``202`` with a
      ``DocumentUploadResponse``; behavior is unchanged from M2 except the
      addition of a file-extension check (415) beside the MIME check
      (double validation per DEV_SPEC).
    - **Batch** (M5): send ``files[]``. All files go to the same
      collection. Returns ``202`` with a ``BatchUploadResponse`` carrying
      a ``batch_id`` and one result per file (``accepted`` → ``task_id``
      to poll, ``skipped`` → duplicate, ``rejected`` → structured error).
      A single file's validation / ingestion failure never blocks the
      others.

    Limits: per-file size ``WEB_API_UPLOAD_MAX_BYTES`` (413), batch file
    count ``WEB_API_UPLOAD_MAX_BATCH_FILES`` and batch total
    ``WEB_API_UPLOAD_MAX_BATCH_BYTES`` (413), extension + MIME allow-lists
    (415). Unknown collection → 404. There is no SSE — clients poll
    ``GET /tasks/{id}`` for each accepted file.
    """
    # ---- 0. Mode dispatch -------------------------------------------
    has_single = file is not None
    has_batch = bool(files)
    if has_single and has_batch:
        raise BadRequestError(
            "Provide either 'file' (single upload) or 'files' (batch), not both",
            details={"single": True, "batch": True},
        )
    if not has_single and not has_batch:
        raise BadRequestError(
            "No file provided: send 'file' for a single upload or "
            "'files[]' for a batch",
            details={"single": False, "batch": False},
        )

    # ---- 1. Collection exists (shared by both modes) ----------------
    name = resolve_collection_name(services, collection_id)
    if name is None:
        raise CollectionNotFoundError(
            f"collection {collection_id} does not exist",
            details={"collection_id": str(collection_id)},
        )

    if has_batch:
        return await _upload_batch(
            files=files,  # type: ignore[arg-type]
            collection=name,
            collection_id=collection_id,
            services=services,
            background_tasks=background_tasks,
        )
    return await _upload_single(
        file=file,  # type: ignore[arg-type]
        collection=name,
        collection_id=collection_id,
        services=services,
    )


async def _upload_single(
    *,
    file: UploadFile,
    collection: str,
    collection_id: UUID,
    services: ApplicationServices,
) -> DocumentUploadResponse:
    """Legacy single-file path — MIME + extension + size checks (HTTP errors)."""
    # ---- 1. MIME allow-list -----------------------------------------
    if file.content_type not in SETTINGS.upload_allowed_mime:
        raise UnsupportedMediaTypeError(
            f"file content-type {file.content_type!r} is not allowed; "
            f"expected one of {sorted(SETTINGS.upload_allowed_mime)}",
            details={
                "received_content_type": file.content_type,
                "allowed_content_types": sorted(SETTINGS.upload_allowed_mime),
            },
        )

    # ---- 2. Extension allow-list (M5 double validation) -------------
    # Must stay 415: ``test_web_api_upload`` asserts a ``.txt`` upload
    # returns ``UNSUPPORTED_MEDIA_TYPE`` even when its MIME passes.
    # ``pathlib.Path`` (not FastAPI's ``Path`` param helper).
    ext = pathlib.Path(file.filename or "").suffix.lower()
    if ext not in SETTINGS.upload_allowed_extensions:
        raise UnsupportedMediaTypeError(
            f"file extension {ext!r} is not allowed; expected one of "
            f"{sorted(SETTINGS.upload_allowed_extensions)}",
            details={
                "received_extension": ext,
                "allowed_extensions": sorted(SETTINGS.upload_allowed_extensions),
            },
        )

    # ---- 3. Size check ----------------------------------------------
    payload = await file.read()
    size = len(payload)
    max_bytes = SETTINGS.upload_max_bytes
    if size > max_bytes:
        raise PayloadTooLargeError(
            f"file size {size} bytes exceeds the {max_bytes}-byte limit",
            details={
                "size_bytes": size,
                "max_bytes": max_bytes,
            },
        )

    # ---- 4. Compute the document id deterministically --------------
    # The source_path is the canonical on-disk path the pipeline will
    # ingest (see ``IngestionService.compute_source_path``). Using a
    # path *without* the task id means re-uploading the same file to
    # the same collection yields the same document id - so the UI can
    # dedupe re-uploads and the document-detail endpoint can re-derive
    # the id from storage consistently.
    source_path = services.ingestion.compute_source_path(
        collection=collection,
        filename=file.filename or "upload.bin",
    )
    doc_uuid = document_uuid(collection, str(source_path))

    # ---- 5. Kick off the task --------------------------------------
    record = services.ingestion.upload(
        bytes_payload=payload,
        filename=file.filename or "upload.bin",
        collection=collection,
        collection_id=collection_id,
        document_id=doc_uuid,
        source_path=source_path,
    )

    # ---- 6. Build the synthetic DocumentDetail ---------------------
    # The document isn't in the integrity DB yet (the pipeline hasn't
    # finished), so we materialise a pending shape from the values we
    # know. ``last_task_id`` is the task id we just created; ``status``
    # is ``pending``; sizes start at 0 - the polling endpoint is the
    # canonical source of truth.
    detail = _pending_document_detail(
        document_id=doc_uuid,
        collection_id=collection_id,
        filename=file.filename or "upload.bin",
        size_bytes=size,
        task_id=record.id,
    )

    return DocumentUploadResponse(document=detail, task_id=record.id)


async def _upload_batch(
    *,
    files: list[UploadFile],
    collection: str,
    collection_id: UUID,
    services: ApplicationServices,
    background_tasks: BackgroundTasks,
) -> BatchUploadResponse:
    """M5 batch path — per-file results, batch-level limits as HTTP errors."""
    # Batch-level limits. Count is cheap to check before reading bodies;
    # the total-size check needs the bytes, so it runs after the read.
    if len(files) > SETTINGS.upload_max_batch_files:
        raise PayloadTooLargeError(
            f"batch has {len(files)} files, exceeding the "
            f"{SETTINGS.upload_max_batch_files}-file limit",
            details={
                "count": len(files),
                "max_files": SETTINGS.upload_max_batch_files,
            },
        )

    items: list[BatchFileUpload] = []
    total_bytes = 0
    for f in files:
        payload = await f.read()
        items.append(
            BatchFileUpload(
                filename=f.filename or "upload.bin",
                content_type=f.content_type,
                bytes_payload=payload,
            ),
        )
        total_bytes += len(payload)

    if total_bytes > SETTINGS.upload_max_batch_bytes:
        raise PayloadTooLargeError(
            f"batch total size {total_bytes} bytes exceeds the "
            f"{SETTINGS.upload_max_batch_bytes}-byte limit",
            details={
                "total_bytes": total_bytes,
                "max_bytes": SETTINGS.upload_max_batch_bytes,
            },
        )

    # Per-file validation + task creation live in the service so the
    # batch semantics (independent rejection / dedup skip / per-file
    # task + trace) are shared with any future non-HTTP caller.
    response = services.ingestion.upload_batch(
        items=items,
        collection=collection,
        collection_id=collection_id,
        defer_workers=True,
    )
    background_tasks.add_task(services.ingestion.start_batch, response.batch_id)
    return response


def _pending_document_detail(
    *,
    document_id: UUID,
    collection_id: UUID,
    filename: str,
    size_bytes: int,
    task_id: UUID,
) -> DocumentDetail:
    """Materialise a ``DocumentDetail`` for a freshly-uploaded document.

    The pipeline hasn't run yet, so chunk_count / image_count are 0,
    file_hash is unknown, and the status is ``pending``. The
    ``last_task_id`` is the task we just created; clients can poll
    ``GET /tasks/{id}`` for the real progress.
    """
    now = datetime.now(tz=timezone.utc)
    return DocumentDetail(
        id=document_id,
        collection_id=collection_id,
        filename=filename,
        size_bytes=size_bytes,
        status="pending",
        chunk_count=0,
        image_count=0,
        created_at=now,
        updated_at=now,
        file_hash=None,
        last_task_id=task_id,
        last_error=None,
    )


__all__ = ["router"]
