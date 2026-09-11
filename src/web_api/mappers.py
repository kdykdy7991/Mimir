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
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from src.application.services.query_service import QueryResult
from src.core.trace.trace_context import (
    derive_stage_status,
    derive_trace_status,
)
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
    """Map a document UUID back to ``(collection, source_path)`` (or ``None``).

    The stable ids are deterministic ``uuid5(collection, source_path)``
    values, so they cannot be inverted without enumerating every key. We
    keep a short-TTL index on :class:`DocumentService` (built from a
    single integrity listing) so this lookup never scans Chroma or the
    image store like the old path did.
    """
    lookup = getattr(services.document, "resolve_document_id", None)
    if callable(lookup):
        return lookup(document_id)
    # Fallback to the legacy full scan for callers with a stub service.
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


def to_document_summary(
    info: DocumentInfo,
    *,
    tags: list | None = None,
    folder_id: str | None = None,
) -> DocumentSummary:
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
        tags=list(tags) if tags else [],
        folder_id=folder_id,
    )


def to_document_detail(
    info: DocumentInfo,
    services=None,
    *,
    chunks: list[dict] | None = None,
) -> DocumentDetail:
    """Map a ``DocumentInfo`` to a ``DocumentDetail`` DTO.

    ``last_task_id`` is filled from the in-memory :class:`TaskTracker`
    when ``services`` is supplied. The router normally passes the
    application services; CLI / dashboard callers that don't have a
    tracker get ``None`` (same behaviour as M1 / batch 1).
    """
    last_task_id = None
    last_error = None
    last_query_id = None
    doc_id = document_uuid(info.collection, info.source_path)
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
        query_lookup = getattr(services.query, "last_query_for_document", None)
        if callable(query_lookup):
            last_query_id = query_lookup(doc_id)
    ordered_chunks = sorted(
        chunks or [],
        key=lambda item: int((item.get("metadata") or {}).get("chunk_index", 0)),
    )
    metadata = (ordered_chunks[0].get("metadata") or {}) if ordered_chunks else {}
    table_ids = {
        str(meta["table_index"])
        for item in ordered_chunks
        if (meta := item.get("metadata") or {}).get("content_type") == "table"
        and meta.get("table_index") is not None
    }
    raw_warnings = metadata.get("parse_warnings") or metadata.get("warnings") or []
    if isinstance(raw_warnings, str):
        raw_warnings = [raw_warnings]
    parse_warnings = [str(value) for value in raw_warnings if str(value).strip()]
    page_count_raw = metadata.get("page_count")
    try:
        page_count = int(page_count_raw) if page_count_raw is not None else None
    except (TypeError, ValueError):
        page_count = None
    try:
        scanned_page_count = int(metadata.get("scanned_page_count") or 0)
    except (TypeError, ValueError):
        scanned_page_count = 0
    vision_processed = None
    if ordered_chunks:
        vision_processed = any(
            (item.get("metadata") or {}).get("content_type") in {"image_ocr", "image_caption"}
            for item in ordered_chunks
        ) or scanned_page_count > 0

    chunk_rows = []
    current_heading = None
    for fallback_index, item in enumerate(ordered_chunks):
        text = str(item.get("text") or "")
        meta = item.get("metadata") or {}
        heading_line = next(
            (line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")),
            None,
        )
        if heading_line:
            current_heading = heading_line
        page_raw = meta.get("page") if meta.get("page") is not None else meta.get("page_num")
        try:
            page = int(page_raw) if page_raw is not None else None
        except (TypeError, ValueError):
            page = None
        chunk_rows.append({
            "index": int(meta.get("chunk_index", fallback_index)),
            "chunk_id": str(item.get("id") or ""),
            "heading": current_heading,
            "page": page,
            "character_count": len(text),
            "content_type": str(meta.get("content_type") or "text"),
        })

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
        table_count=len(table_ids),
        parse_warnings=parse_warnings,
        parser_engine=(str(metadata["parser_engine"]) if metadata.get("parser_engine") else None),
        parse_status=(str(metadata["parse_status"]) if metadata.get("parse_status") else None),
        page_count=page_count,
        vision_processed=vision_processed,
        chunks=chunk_rows,
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

_TERMINAL_TASK_STATUSES = {"succeeded", "failed", "cancelled", "skipped"}

# Known secret-ish substrings to scrub from human-readable error strings
# before they surface on the wire. The stage ``details`` dict never carries
# the raw ``error``; only the sanitised ``error_summary`` is exposed.
# Ordered so structured tokens (sk-… keys, bearer tokens, ``key=value``)
# are removed before generic base64/long-hex material.
_SENSITIVE_ERROR_PATTERNS = [
    re.compile(r"(?i)\bsk-[A-Za-z0-9]{8,}"),          # OpenAI-style keys
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{6,}"),  # bearer tokens
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|token)\b"
               r"[=: ]+'?[A-Za-z0-9_\-.]{4,}"),
    re.compile(r"(?i)\bauthorization\b[:\s]+[^\s,]+"),
    re.compile(r"[A-Za-z0-9+/]{48,}={0,3}"),          # long key/base64 material
]
_ERROR_SUMMARY_MAX = 500


def _sanitize_error_summary(value: object | None) -> str | None:
    """Redact secrets and truncate a raw error value into a safe summary."""
    if value is None:
        return None
    text = str(value)
    for pattern in _SENSITIVE_ERROR_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = " ".join(text.split())
    if not text:
        return None
    if len(text) > _ERROR_SUMMARY_MAX:
        text = text[:_ERROR_SUMMARY_MAX] + "…"
    return text


def _trace_attempt(raw: dict) -> int | None:
    direct = raw.get("attempt")
    if direct is not None:
        return int(direct)
    meta = raw.get("metadata") or {}
    if meta.get("attempt") is not None:
        return int(meta["attempt"])
    return None


def to_trace_response(raw: dict) -> TraceResponse:
    """Map a ``TraceContext.to_dict()`` (or TraceStore record) to a
    ``TraceResponse``.

    Stages carrying ``elapsed_ms`` are surfaced — those are the
    orchestrator-level bracketing stages (``dense_retrieval``,
    ``sparse_retrieval``, ``fusion``, ...). Raw retriever start/finish
    sub-events (which have no duration) stay out of the wire response.
    The ingestion ``skipped`` event is the one exception: it is a real
    terminal outcome that the UI must explain, so it is returned with a
    zero duration rather than being mistaken for a missing trace.
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
            is_ingestion_skip = (
                raw.get("trace_type") == "ingestion"
                and stage.get("event") == "skipped"
            )
            if not is_ingestion_skip:
                continue  # not a bracketing stage — skip (see docstring)
            elapsed = 0.0
        details = {
            k: v for k, v in stage.items()
            if k not in (
                "name", "ts", "elapsed_ms", "method", "provider",
                "input_count", "output_count", "attempt",
                "skip_reason", "error_code", "error_summary", "error",
            )
        }
        stage_status = derive_stage_status(stage)
        stages.append(TraceStage(
            name=stage["name"],
            method=stage.get("method"),
            provider=stage.get("provider"),
            started_at=datetime.fromtimestamp(
                float(stage["ts"]), tz=timezone.utc,
            ),
            duration_ms=float(elapsed),
            details=details,
            status=stage_status,
            input_count=(int(stage["input_count"])
                         if stage.get("input_count") is not None
                         else (int(stage["n_in"]) if stage.get("n_in") is not None else None)),
            output_count=(int(stage["output_count"])
                          if stage.get("output_count") is not None
                          else (int(stage["n_out"]) if stage.get("n_out") is not None else None)),
            attempt=(int(stage["attempt"])
                     if stage.get("attempt") is not None
                     else _trace_attempt(raw)),
            skip_reason=stage.get("skip_reason"),
            error_code=stage.get("error_code")
            or ("STAGE_ERROR" if stage.get("event") == "error" else None),
            error_summary=_sanitize_error_summary(
                stage.get("error_summary") or stage.get("error"),
            ),
        ))
    status = derive_trace_status(raw)
    return TraceResponse(
        id=UUID(raw["trace_id"]),
        trace_type=raw.get("trace_type", "query"),
        started_at=started,
        finished_at=finished,
        total_latency_ms=float(raw.get("total_elapsed_ms") or 0.0),
        stages=stages,
        error=_sanitize_error_summary(raw.get("error")),
        status=status,
        retryable=status in {"failed", "canceled"},
        cancelable=status in {"pending", "running"},
        attempt=_trace_attempt(raw),
        parent_trace_id=raw.get("parent_trace_id")
        or (raw.get("metadata") or {}).get("parent_trace_id"),
    )


_TASK_TO_TRACE_STATUS = {
    "pending": "pending",
    "running": "running",
    "succeeded": "success",
    "failed": "failed",
    "cancelled": "canceled",
    "skipped": "skipped",
}


def build_live_trace_response(task, raw: dict | None) -> TraceResponse:
    """Merge a live ingestion task with its stored/loaded trace.

    Merge rule (B3.2):

    - The DURABLE (JSONL/indexed) trace is the baseline for ``stages``.
    - A TERMINAL task status OR a terminal status already persisted on the
      trace wins — the timeline never regresses. Live ``running`` state is
      only layered on top when the task is still in flight.
    - While the task is running, the current in-progress stage (from
      ``task.progress.stage``) is appended as a ``running`` stage so the
      frontend sees completed stages + the one being worked on.
    - When the task exists but no trace was ever recorded, a backward
      compatible empty trace carrying the task status is returned.
    """
    resp = to_trace_response(raw) if raw is not None else _empty_trace_response(task)

    task_terminal = task.status in _TERMINAL_TASK_STATUSES
    stored_terminal = resp.status in {"success", "failed", "canceled", "skipped"}

    # Final state wins: a terminal task or a terminal persisted status is
    # authoritative and never regresses to a live ``running``/``pending``.
    if task_terminal:
        status = _TASK_TO_TRACE_STATUS.get(task.status, resp.status)
    elif stored_terminal:
        status = resp.status
    elif task.status == "pending":
        status = "pending"
    else:
        status = "running"
    resp.status = status
    resp.retryable = status in {"failed", "canceled"}
    resp.cancelable = status in {"pending", "running"}

    if task.attempt is not None:
        resp.attempt = task.attempt
    parent = getattr(task, "parent_task_id", None)
    if parent is not None:
        resp.parent_trace_id = str(parent)

    # Live current stage: only when the task is genuinely in flight.
    if not task_terminal and task.progress is not None and task.progress.stage:
        current = task.progress.stage
        if not (resp.stages
                and resp.stages[-1].name == current
                and resp.stages[-1].status == "running"):
            resp.stages.append(TraceStage(
                name=current,
                started_at=task.updated_at,
                duration_ms=0.0,
                details={},
                status="running",
            ))
    return resp


def _empty_trace_response(task) -> TraceResponse:
    """Build a 200 TraceResponse from a task record that has no trace."""
    started = task.created_at
    end = task.finished_at or task.updated_at or task.created_at
    total_ms = max(0.0, (end - started).total_seconds() * 1000.0)
    status = _TASK_TO_TRACE_STATUS.get(task.status, "running")
    return TraceResponse(
        id=task.id,
        trace_type="ingestion",
        started_at=started,
        finished_at=end,
        total_latency_ms=total_ms,
        stages=[],
        error=task.error.message if task.error is not None else None,
        status=status,
        retryable=status in {"failed", "canceled"},
        cancelable=status in {"pending", "running"},
        attempt=task.attempt if task.attempt else None,
        parent_trace_id=(
            str(task.parent_task_id) if getattr(task, "parent_task_id", None) else None
        ),
    )


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def decode_offset_cursor(cursor: str | None) -> int:
    """Decode the base64 opaque offset cursor (garbage → 0)."""
    if not cursor:
        return 0
    try:
        return int(base64.b64decode(cursor.encode()).decode())
    except Exception:  # noqa: BLE001 — a garbage cursor just resets.
        return 0


def build_cursor_page_info(
    offset: int, page_length: int, total: int,
) -> PageInfo:
    """Build ``PageInfo`` for a server-side paged listing.

    ``has_more`` is derived from the known ``total`` rather than by
    loading an extra row. ``total`` may be ``-1`` (unknown) in which case
    we fall back to assuming a further page exists when the page is full.
    """
    next_offset = offset + page_length
    if total >= 0:
        has_more = next_offset < total
    else:
        has_more = page_length > 0
    next_cursor = (
        base64.b64encode(str(next_offset).encode()).decode()
        if has_more else None
    )
    return PageInfo(next_cursor=next_cursor, has_more=has_more)


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
    "build_cursor_page_info",
    "decode_offset_cursor",
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
    "build_live_trace_response",
]
