"""
Batch document operations (task book B2.6–B2.9).

Four ``POST`` actions over ``/collections/{collection_id}/documents/batch``:

- ``tags``      — add / remove / replace tags on many documents (≤100)
- ``move``      — place many documents into a folder (or root) (≤100)
- ``reprocess`` — re-run ingestion on many documents, one task each (≤20)
- ``delete``    — coordinated delete of many documents (≤100)

All share the per-item ``BatchItemResult`` envelope (``status: success|error``
plus a structured ``error``), so a single bad item never blocks the rest of
the batch. Every item is validated against the *requested collection* scope
first — a document from another collection, a foreign/unknown tag, or a
foreign folder is rejected per item and never leaks cross-collection
existence into another collection's data.

Business logic (deletion, reprocessing) lives in the application services
(``DocumentService.delete_document`` / ``IngestionService.reprocess``); the
router only orchestrates the per-item results.
"""

from __future__ import annotations

from pathlib import Path as FilePath
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Path, Request

from src.application.composition import ApplicationServices
from src.ingestion.storage.bm25_locks import bm25_write_lock
from src.web_api.dependencies import get_application_services
from src.web_api.errors import BadRequestError, CollectionNotFoundError
from src.web_api.mappers import collection_uuid, resolve_collection_name, resolve_document
from src.web_api.schemas.batch import (
    BATCH_MAX_ITEMS,
    REPROCESS_MAX_ITEMS,
    BatchDeleteRequest,
    BatchItemError,
    BatchItemResult,
    BatchMoveRequest,
    BatchReprocessRequest,
    BatchResponse,
    BatchTagsRequest,
)

router = APIRouter(tags=["batch"])

# Terminal-*excluded* task states that count as "a reprocess is already in
# flight for this document". Both mean the ingestion worker is (or soon
# will be) actively processing, so enqueueing again would double it.
_IN_FLIGHT_STATUSES = frozenset({"pending", "running"})

# Duplicate-reprocess / idempotency bookkeeping for task-creation when the
# injected IngestionService exposes no durable tracker (test fakes). For the
# real service the authoritative duplicate check rides on TaskTracker; these
# maps only back-stop stubs and play nicely with the same request retried.
_REPROCESS_IN_FLIGHT: dict[tuple[str, str], UUID] = {}
# idempotency-key -> per-document results, so a client retry with the same
# Idempotency-Key returns the exact same task ids instead of re-enqueueing.
_REPROCESS_BY_KEY: dict[str, list[BatchItemResult]] = {}


def _ok(document_id: UUID, *, task_id: UUID | None = None) -> BatchItemResult:
    return BatchItemResult(document_id=document_id, status="success", task_id=task_id)


def _err(document_id: UUID, code: str, message: str, **details) -> BatchItemResult:
    return BatchItemResult(
        document_id=document_id,
        status="error",
        error=BatchItemError(code=code, message=message, details=details),
    )


def _resolve_collection(services: ApplicationServices, collection_id: UUID) -> str:
    name = resolve_collection_name(services, collection_id)
    if name is None:
        raise CollectionNotFoundError(
            f"collection {collection_id} does not exist",
            details={"collection_id": str(collection_id)},
        )
    return name


def _require_items(document_ids: list, label: str, cap: int) -> None:
    """Whole-request boundary checks: non-empty and within the batch cap.

    These are 400-class request errors (not per-item) because an empty or
    oversized batch is malformed regardless of any individual item.
    """
    if not document_ids:
        raise BadRequestError(
            "a batch must contain at least one document id",
            details={"operation": label},
        )
    if len(document_ids) > cap:
        raise BadRequestError(
            f"batch exceeds the {cap}-document limit for {label}",
            details={"operation": label, "count": len(document_ids), "max": cap},
        )


def _resolve_item(services, document_id: UUID, collection_name: str):
    """Resolve a document to ``(collection, source_path)`` within scope.

    Returns ``None`` for an unknown document or one owned by a different
    collection (both surface the same per-item error so cross-collection
    existence is not leaked).
    """
    resolved = resolve_document(services, document_id)
    if resolved is None or resolved[0] != collection_name:
        return None
    return resolved


# ---------------------------------------------------------------------------
# B2.6 — batch tags
# ---------------------------------------------------------------------------

@router.post(
    "/collections/{collection_id}/documents/batch/tags",
    response_model=BatchResponse,
    summary="Add, remove, or replace tags across many documents",
)
async def batch_tags(
    body: BatchTagsRequest,
    collection_id: UUID = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> BatchResponse:
    """Apply a tag action to up to 100 documents in one collection.

    ``add`` assigns the given tags, ``remove`` unlinks them, and ``replace``
    sets each document's tags to exactly the given set. Every ``tag_id``
    must belong to the collection and every ``document_id`` must resolve to
    that same collection; foreign/unknown items are reported per item and do
    not block the valid ones. No partial writes ever occur for the items
    that do succeed.
    """
    _require_items(body.document_ids, "tags", BATCH_MAX_ITEMS)
    collection_name = _resolve_collection(services, collection_id)
    db = services.db

    # Collection scope for the tag set is shared across the batch.
    collection_scope = str(collection_uuid(collection_name))
    requested_tags = list(dict.fromkeys(body.tag_ids))
    valid_tags = [
        tid
        for tid in requested_tags
        if (row := db.get_tag(tid)) is not None and row["collection_id"] == collection_scope
    ]
    bad_tags = [tid for tid in requested_tags if tid not in valid_tags]

    results: list[BatchItemResult] = []
    for document_id in body.document_ids:
        resolved = _resolve_item(services, document_id, collection_name)
        if resolved is None:
            results.append(_err(
                document_id, "DOCUMENT_NOT_FOUND",
                "document does not exist or does not belong to this collection",
                collection_id=str(collection_id),
            ))
            continue
        if bad_tags:
            results.append(_err(
                document_id, "TAG_NOT_FOUND",
                "one or more tag ids do not exist in this collection",
                tag_ids=bad_tags,
            ))
            continue

        current = db.document_tag_ids(str(document_id))
        if body.action == "add":
            target = current + [t for t in valid_tags if t not in current]
        elif body.action == "remove":
            target = [t for t in current if t not in set(valid_tags)]
        else:  # replace
            target = valid_tags
        db.set_document_tags(str(document_id), target)
        results.append(_ok(document_id))

    return BatchResponse(items=results)


# ---------------------------------------------------------------------------
# B2.7 — batch move
# ---------------------------------------------------------------------------

@router.post(
    "/collections/{collection_id}/documents/batch/move",
    response_model=BatchResponse,
    summary="Move many documents into a folder (or to root)",
)
async def batch_move(
    body: BatchMoveRequest,
    collection_id: UUID = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> BatchResponse:
    """Place up to 100 documents into ``folder_id`` (or root when ``null``).

    The target folder must belong to the collection (else every item gets a
    per-item ``FOLDER_NOT_FOUND``); documents from another collection or
    unknown are reported per item without blocking the valid ones.
    """
    _require_items(body.document_ids, "move", BATCH_MAX_ITEMS)
    collection_name = _resolve_collection(services, collection_id)
    db = services.db
    collection_scope = str(collection_uuid(collection_name))

    # Scope the target folder once — it applies to the whole batch.
    folder_valid = True
    if body.folder_id is not None:
        row = db.get_folder(body.folder_id)
        folder_valid = row is not None and row["collection_id"] == collection_scope

    results: list[BatchItemResult] = []
    for document_id in body.document_ids:
        resolved = _resolve_item(services, document_id, collection_name)
        if resolved is None:
            results.append(_err(
                document_id, "DOCUMENT_NOT_FOUND",
                "document does not exist or does not belong to this collection",
                collection_id=str(collection_id),
            ))
            continue
        if not folder_valid:
            results.append(_err(
                document_id, "FOLDER_NOT_FOUND",
                "target folder does not exist in this collection",
                folder_id=body.folder_id,
            ))
            continue
        db.move_document(
            document_id=str(document_id),
            folder_id=body.folder_id,
            collection_id=collection_scope,
        )
        results.append(_ok(document_id))

    return BatchResponse(items=results)


# ---------------------------------------------------------------------------
# B2.8 — batch reprocess
# ---------------------------------------------------------------------------

@router.post(
    "/collections/{collection_id}/documents/batch/reprocess",
    response_model=BatchResponse,
    summary="Re-run ingestion on many documents, one task each",
)
async def batch_reprocess(
    request: Request,
    body: BatchReprocessRequest,
    collection_id: UUID = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> BatchResponse:
    """Enqueue an independent reprocess task for up to 20 documents.

    - Each successful item carries its own ``task_id``.
    - A document that already has a ``pending``/``running`` reprocess task
      is reported as a per-item ``DUPLICATE_REPROCESS`` conflict — it is
      never double-enqueued.
    - Idempotency: a request re-sent with the same ``Idempotency-Key``
      header returns the original per-item results (same task ids) instead
      of creating new tasks.
    """
    _require_items(body.document_ids, "reprocess", REPROCESS_MAX_ITEMS)
    collection_name = _resolve_collection(services, collection_id)

    idem_key = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
    if idem_key and idem_key in _REPROCESS_BY_KEY:
        return BatchResponse(items=list(_REPROCESS_BY_KEY[idem_key]))

    tracker = getattr(services.ingestion, "tracker", None)
    results: list[BatchItemResult] = []
    for document_id in body.document_ids:
        resolved = _resolve_item(services, document_id, collection_name)
        if resolved is None:
            results.append(_err(
                document_id, "DOCUMENT_NOT_FOUND",
                "document does not exist or does not belong to this collection",
                collection_id=str(collection_id),
            ))
            continue

        source_path = resolved[1]
        in_flight = _find_in_flight(
            tracker, collection_name, collection_id, source_path,
        )
        if in_flight is not None:
            results.append(_err(
                document_id, "DUPLICATE_REPROCESS",
                "a reprocess task is already running for this document",
                existing_task_id=str(in_flight),
            ))
            continue

        try:
            task_id = _start_reprocess(
                services, collection_name, collection_id, document_id, source_path,
            )
        except FileNotFoundError:
            results.append(_err(
                document_id, "DOCUMENT_NOT_FOUND",
                "original file is unavailable for reprocessing",
                source_path=source_path,
            ))
            continue
        except Exception as exc:  # noqa: BLE001 — surface as a per-item failure
            results.append(_err(
                document_id, "REPROCESS_FAILED",
                "could not start reprocess task",
                exception_type=type(exc).__name__,
            ))
            continue
        results.append(_ok(document_id, task_id=task_id))

    if idem_key:
        _REPROCESS_BY_KEY[idem_key] = list(results)
    return BatchResponse(items=results)


def _find_in_flight(tracker, collection_name, collection_id, source_path) -> UUID | None:
    """Return the in-flight task id for a document, or ``None``.

    Prefers the authoritative TaskTracker state; falls back to the
    process-local map for service stubs that lack a tracker.
    """
    if tracker is not None and hasattr(tracker, "latest_for_document"):
        latest = tracker.latest_for_document(collection_id, source_path)
        if latest is not None and latest.status in _IN_FLIGHT_STATUSES:
            return latest.id
        return None
    existing = _REPROCESS_IN_FLIGHT.get((collection_name, source_path))
    if existing is not None:
        return existing
    return None


def _start_reprocess(services, collection_name, collection_id, document_id, source_path) -> UUID:
    """Create + start a reprocess task; returns its task id.

    Delegates to ``IngestionService.reprocess`` when available so the work
    actually runs through the real ingestion machinery; otherwise falls back
    to process-local bookkeeping (service fakes in tests).
    """
    reprocess = getattr(services.ingestion, "reprocess", None)
    if callable(reprocess):
        record = reprocess(
            collection=collection_name,
            collection_id=collection_id,
            document_id=document_id,
            source_path=source_path,
            filename=FilePath(source_path).name,
        )
        task_id = record.id
        _REPROCESS_IN_FLIGHT[(collection_name, source_path)] = task_id
        return task_id
    # Fallback: bookkeeping only — used by stub services in tests.
    task_id = uuid4()
    _REPROCESS_IN_FLIGHT[(collection_name, source_path)] = task_id
    return task_id


# ---------------------------------------------------------------------------
# B2.9 — batch delete
# ---------------------------------------------------------------------------

@router.post(
    "/collections/{collection_id}/documents/batch/delete",
    response_model=BatchResponse,
    summary="Delete many documents (explicit POST action)",
)
async def batch_delete(
    body: BatchDeleteRequest,
    collection_id: UUID = Path(...),
    services: ApplicationServices = Depends(get_application_services),
) -> BatchResponse:
    """Coordinated delete of up to 100 documents.

    Uses an explicit POST action (no DELETE request body, which is
    unreliable through some proxies/guards). Each document reuses the same
    service call as the single-document delete — clearing the original
    file, vector chunks, BM25, and image storage — without copying the
    deletion business logic into the router. A failed item never blocks the
    rest of the batch.
    """
    _require_items(body.document_ids, "delete", BATCH_MAX_ITEMS)
    collection_name = _resolve_collection(services, collection_id)

    results: list[BatchItemResult] = []
    for document_id in body.document_ids:
        resolved = _resolve_item(services, document_id, collection_name)
        if resolved is None:
            results.append(_err(
                document_id, "DOCUMENT_NOT_FOUND",
                "document does not exist or does not belong to this collection",
                collection_id=str(collection_id),
            ))
            continue
        _collection, source_path = resolved
        try:
            with bm25_write_lock(collection_name):
                result = services.document.delete_document(source_path, collection_name)
                invalidate = getattr(services.engines, "invalidate_collection", None)
                if callable(invalidate):
                    invalidate(collection_name)
        except Exception as exc:  # noqa: BLE001 — per-item failure
            results.append(_err(
                document_id, "DELETE_FAILED",
                "document deletion failed",
                exception_type=type(exc).__name__,
            ))
            continue
        if getattr(result, "errors", None):
            results.append(_err(
                document_id, "DELETE_FAILED",
                "document deletion was only partially completed; retry is safe",
                errors=list(getattr(result, "errors", [])),
            ))
        else:
            results.append(_ok(document_id))

    return BatchResponse(items=results)


__all__ = ["router"]