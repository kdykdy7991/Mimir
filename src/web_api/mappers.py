"""
Domain → DTO mapping + stable resource IDs for the Web API (M2).

The v0.1 contract exposes collections / documents as UUIDs, but the
storage identifies them by *name* (collection) and *source_path*
(document). This module derives stable UUIDs from those storage keys
(via ``uuid5``) so:

- the API is contract-shaped (UUID ids, ``...Z`` timestamps),
- ids are deterministic across restarts (no registry table needed),
- resolution (UUID → storage key) is an enumeration, cheap at local scale.

Timestamps: collections have no durable creation record, so we map the
BM25 index file's ctime / mtime; documents carry their integrity-record
timestamps.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from src.application.services.query_service import QueryResult
from src.ingestion.document_manager import CollectionRef, CollectionStats, DocumentInfo
from src.web_api.schemas.collections import CollectionSummary
from src.web_api.schemas.common import PageInfo
from src.web_api.schemas.documents import DocumentDetail, DocumentSummary
from src.web_api.schemas.queries import (
    Citation,
    CitationImage,
    CitationScores,
    QueryDiagnostics,
    QueryResponse,
)
from src.web_api.schemas.traces import TraceResponse, TraceStage

# Stable ids live in the application layer (QueryService derives
# document ids for query-result recording without importing the Web
# API). Re-exported here so routers / mappers keep their imports.
from src.application.identifiers import (  # noqa: E402
    collection_uuid,
    document_uuid,
)


def resolve_collection_name(services, collection_id: UUID) -> str | None:
    """Map a collection UUID back to its name (or ``None``)."""
    for ref in services.document.list_collections():
        if collection_uuid(ref.name) == collection_id:
            return ref.name
    return None


def resolve_document(services, document_id: UUID) -> tuple[str, str] | None:
    """Map a document UUID back to ``(collection, source_path)`` (or ``None``)."""
    # M3: document ids are derived from (collection, source_path), so a
    # non-default collection's document would never resolve against the
    # default listing. Scan every known collection instead.
    for ref in services.document.list_collections():
        for info in services.document.list_documents(collection=ref.name):
            if document_uuid(info.collection, info.source_path) == document_id:
                return info.collection, info.source_path
    return None


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def _ts(value: float | None) -> datetime:
    """Unix seconds → timezone-aware UTC datetime (epoch fallback)."""
    if value is None:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromtimestamp(value, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

def to_collection_summary(
    ref: CollectionRef,
    stats: CollectionStats,
    description: str | None = None,
) -> CollectionSummary:
    return CollectionSummary(
        id=collection_uuid(ref.name),
        name=ref.name,
        description=description,
        document_count=stats.n_documents,
        chunk_count=stats.n_chunks,
        created_at=_ts(ref.created_at),
        updated_at=_ts(ref.updated_at),
    )


def to_collection_detail(
    ref: CollectionRef,
    stats: CollectionStats,
    description: str | None = None,
):
    from src.web_api.schemas.collections import CollectionDetail

    return CollectionDetail(**to_collection_summary(
        ref, stats, description=description,
    ).model_dump())


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def _document_status(storage_status: str) -> str:
    """Map the integrity record's status to the contract's ``DocumentStatus``."""
    return {"success": "ready", "failed": "failed"}.get(
        storage_status, "pending",
    )


def to_document_summary(info: DocumentInfo) -> DocumentSummary:
    return DocumentSummary(
        id=document_uuid(info.collection, info.source_path),
        collection_id=collection_uuid(info.collection),
        filename=Path(info.source_path).name,
        size_bytes=info.file_size or 0,
        status=_document_status(info.status),
        chunk_count=info.n_chunks,
        image_count=info.n_images,
        created_at=_ts(info.created_at or info.last_modified),
        updated_at=_ts(info.updated_at or info.last_modified),
    )


def to_document_detail(info: DocumentInfo, services=None) -> DocumentDetail:
    """Map a ``DocumentInfo`` to a ``DocumentDetail`` DTO.

    ``last_task_id`` is filled from the in-memory :class:`TaskTracker`
    when ``services`` is supplied. The router normally passes the
    application services; CLI / dashboard callers that don't have a
    tracker get ``None`` (same behaviour as M1 / batch 1).
    """
    last_task_id = None
    last_error = None
    last_query_id = None
    if services is not None:
        # Storage is keyed by (collection, source_path). The tracker
        # indexes by (collection_id, source_path), so we materialise
        # the collection UUID here instead of asking the caller.
        cid = collection_uuid(info.collection)
        task = services.ingestion.get_latest_task_for(cid, info.source_path)
        if task is not None:
            last_task_id = task.id
            last_error = task.error
        # M3 batch 2: most recent query that cited this document.
        # Guarded because some test fixtures stub ``services.query``
        # with a bare ``object()``.
        doc_id = document_uuid(info.collection, info.source_path)
        query_lookup = getattr(services.query, "last_query_for_document", None)
        if callable(query_lookup):
            last_query_id = query_lookup(doc_id)
    return DocumentDetail(
        id=doc_id,
        collection_id=collection_uuid(info.collection),
        filename=Path(info.source_path).name,
        size_bytes=info.file_size or 0,
        status=_document_status(info.status),
        chunk_count=info.n_chunks,
        image_count=info.n_images,
        created_at=_ts(info.created_at or info.last_modified),
        updated_at=_ts(info.updated_at or info.last_modified),
        file_hash=info.file_hash,
        last_task_id=last_task_id,
        last_query_id=last_query_id,
        last_error=last_error,
    )


# ---------------------------------------------------------------------------
# Queries (M2 batch 3)
# ---------------------------------------------------------------------------

def to_citation_image(img) -> CitationImage:
    """Map a chunk-metadata image entry (``ImageRef`` or dict) to a
    ``CitationImage``. ``url`` is a relative path the Web UI resolves
    against its API base — never a filesystem path."""
    if isinstance(img, dict):
        iid = img.get("id") or ""
        caption = img.get("caption")
    else:
        iid = getattr(img, "id", "") or ""
        caption = getattr(img, "caption", None)
    return CitationImage(
        id=iid,
        url=f"/api/v1/images/{iid}",
        caption=caption,
    )


def to_citation(result, index: int, collection: str) -> Citation:
    """Map one ``RetrievalResult`` to a ``Citation``.

    ``document_id`` is derived from ``(collection, chunk.metadata.source_path)``
    — the same stable id the documents endpoints expose. Scores: only the
    source branch's score is populated (hybrid→fusion, dense→dense,
    sparse→sparse); the others stay ``None`` because a single
    ``RetrievalResult`` carries no per-branch history.
    """
    chunk = result.chunk
    meta = chunk.metadata or {}
    source_path = str(meta.get("source_path") or "")
    scores = CitationScores()
    if result.source == "dense":
        scores.dense = result.score
    elif result.source == "sparse":
        scores.sparse = result.score
    else:
        scores.fusion = result.score

    page = meta.get("page")
    return Citation(
        index=index,
        chunk_id=chunk.id,
        document_id=document_uuid(collection, source_path),
        document_name=Path(source_path).name or "unknown",
        page=int(page) if isinstance(page, (int, float)) else page,
        text=chunk.text or "",
        scores=scores,
        images=[to_citation_image(img) for img in (meta.get("images") or [])],
    )


def to_query_response(result: QueryResult, collection: str) -> QueryResponse:
    """Map a ``QueryResult`` to the ``QueryResponse`` DTO.

    ``query_id`` and ``diagnostics.trace_id`` are the same value — the
    trace id — so the UI can link a query to its trace with one id.
    """
    trace_uuid = UUID(result.trace_id) if result.trace_id else None
    return QueryResponse(
        query_id=trace_uuid or uuid4(),
        answer=None,  # v0.2: retrieval only; answer generation is out of scope
        citations=[
            to_citation(r, i, collection)
            for i, r in enumerate(result.chunks, start=1)
        ],
        diagnostics=QueryDiagnostics(
            duration_ms=result.latency_ms,
            trace_id=trace_uuid,
            dense_count=result.dense_count,
            sparse_count=result.sparse_count,
            fused_count=result.fused_count,
            reranked_count=None,  # rerank not wired into the engine (v0.2)
            degraded=result.degraded,
            degraded_reasons=result.degraded_reasons,
        ),
    )


# ---------------------------------------------------------------------------
# Traces (M2 batch 3)
# ---------------------------------------------------------------------------

def to_trace_response(raw: dict) -> TraceResponse:
    """Map a ``TraceContext.to_dict()`` (or TraceStore record) to a
    ``TraceResponse``.

    Only stages carrying ``elapsed_ms`` are surfaced — those are the
    orchestrator-level bracketing stages (``dense_retrieval``,
    ``sparse_retrieval``, ``fusion``, ...). Raw retriever start/finish
    sub-events (which have no duration) stay out of the wire response.
    """
    started = datetime.fromtimestamp(float(raw["started_at"]), tz=timezone.utc)
    finished_raw = raw.get("finished_at")
    finished = (
        datetime.fromtimestamp(float(finished_raw), tz=timezone.utc)
        if finished_raw is not None else started
    )
    stages: list[TraceStage] = []
    for stage in raw.get("stages") or []:
        elapsed = stage.get("elapsed_ms")
        if elapsed is None:
            continue  # not a bracketing stage — skip (see docstring)
        details = {
            k: v for k, v in stage.items()
            if k not in ("name", "ts", "elapsed_ms", "method", "provider")
        }
        stages.append(TraceStage(
            name=stage["name"],
            method=stage.get("method"),
            provider=stage.get("provider"),
            started_at=datetime.fromtimestamp(
                float(stage["ts"]), tz=timezone.utc,
            ),
            duration_ms=float(elapsed),
            details=details,
        ))
    return TraceResponse(
        id=UUID(raw["trace_id"]),
        trace_type=raw.get("trace_type", "query"),
        started_at=started,
        finished_at=finished,
        total_latency_ms=float(raw.get("total_elapsed_ms") or 0.0),
        stages=stages,
        error=raw.get("error"),
    )


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def paginate(
    items: list,
    cursor: str | None,
    limit: int,
) -> tuple[list, PageInfo]:
    """Offset-based opaque cursor pagination (local-scale)."""
    offset = 0
    if cursor:
        try:
            offset = int(base64.b64decode(cursor.encode()).decode())
        except Exception:  # noqa: BLE001 — a garbage cursor just resets.
            offset = 0
    page = items[offset:offset + limit]
    next_offset = offset + len(page)
    has_more = next_offset < len(items)
    next_cursor = (
        base64.b64encode(str(next_offset).encode()).decode()
        if has_more else None
    )
    return page, PageInfo(next_cursor=next_cursor, has_more=has_more)


__all__ = [
    "collection_uuid",
    "document_uuid",
    "paginate",
    "resolve_collection_name",
    "resolve_document",
    "to_citation",
    "to_citation_image",
    "to_collection_detail",
    "to_collection_summary",
    "to_document_detail",
    "to_document_summary",
    "to_query_response",
    "to_trace_response",
]
