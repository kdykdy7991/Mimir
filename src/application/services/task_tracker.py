"""
In-memory task registry for the M2 Web API.

Each ingestion-triggered document upload gets a :class:`TaskRecord`
that walks the state machine ``pending → running → succeeded | failed``
(see ``src/web_api/schemas/tasks.py`` for the contract). The tracker
owns:

- A registry of ``task_id`` → record, populated by the upload endpoint
  and read by the polling endpoint.
- A ``(collection, source_path)`` → latest task-id index, so the
  document-detail endpoint can surface ``last_task_id`` without scanning
  the whole registry.
- Thread-safe state mutations, since the worker runs in a background
  thread (``IngestionService.upload``).

Persistence is intentionally **out of scope** for v0.1. The contract
says "v0.2" is when SQLite-backed tasks land; until then the registry
is process-local and reset on restart. The upload endpoint returns the
task ID immediately so the frontend can start polling before the worker
begins, and the polling endpoint simply reads the current snapshot —
no replay, no offline tracing.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from src.application.services.task_types import (
    TaskError,
    TaskProgress,
    TaskStage,
    TaskStatus,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Timezone-aware UTC now — shared by all timestamp sites."""
    return datetime.now(tz=timezone.utc)


@dataclass
class TaskRecord:
    """One in-memory ingestion task.

    Mirrors the v0.1 ``TaskStatusResponse`` schema. Mutable so the
    worker thread can flip ``status`` and bump ``updated_at``; the
    tracker's lock guards every mutation.
    """

    id: UUID
    document_id: UUID | None
    collection_id: UUID
    source_path: str           # internal path the pipeline reads
    filename: str              # original filename (for diagnostics)
    task_type: str = "ingestion"       # "ingestion" | "query" (M3 batch 2)
    status: TaskStatus = "pending"
    progress: TaskProgress | None = None
    attempt: int = 0
    error: TaskError | None = None
    # B3 retry/cancel. ``parent_task_id`` links a retry child to the task it
    # restarted (deterministic child id makes retries idempotent).
    # ``cancel_requested`` is the cooperative-cancel flag the worker polls
    # at stage boundaries.
    parent_task_id: UUID | None = None
    cancel_requested: bool = False
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None

    # --- transitions ----------------------------------------------------

    def mark_started(self) -> None:
        """Transition pending → running without a progress stage.

        Ingestion tasks drive ``mark_running(stage)``; query tasks have
        no ingestion stages, so they flip to ``running`` with a bare
        status (M3 batch 2 async queries).
        """
        self.status = "running"
        self.updated_at = _utcnow()

    def mark_running(self, stage: TaskStage) -> None:
        """Transition pending → running with the first stage."""
        self.status = "running"
        self.progress = TaskProgress(stage=stage, current=0, total=None, percent=0)
        self.updated_at = _utcnow()

    def update_progress(
        self,
        stage: TaskStage,
        current: int | None,
        total: int | None,
        percent: int,
        message: str | None = None,
    ) -> None:
        """Refresh the in-flight ``progress`` block."""
        self.progress = TaskProgress(
            stage=stage,
            current=current,
            total=total,
            percent=percent,
            message=message,
        )
        self.updated_at = _utcnow()

    def mark_succeeded(self) -> None:
        """Transition running → succeeded and freeze the timestamp."""
        self.status = "succeeded"
        self.finished_at = _utcnow()
        self.updated_at = self.finished_at
        self.progress = None

    def mark_failed(self, error: TaskError) -> None:
        """Transition running → failed with a structured ``TaskError``."""
        self.status = "failed"
        self.error = error
        self.finished_at = _utcnow()
        self.updated_at = self.finished_at

    def mark_skipped(self) -> None:
        """Transition running → skipped (M5: duplicate of an ingested file).

        The pipeline detected the file's hash was already marked success
        for this collection, so no work was performed. ``skipped`` is a
        terminal state — the polling endpoint returns it like
        ``succeeded``/``failed`` so the frontend stops polling.
        """
        self.status = "skipped"
        self.progress = None
        self.finished_at = _utcnow()
        self.updated_at = self.finished_at

    def mark_cancelled(self) -> None:
        """Transition to the terminal ``cancelled`` state (cooperative cancel).

        Completed atomic writes are never rolled back; this only records
        that the worker stopped at the next safe boundary. Idempotent.
        """
        if self.status in ("cancelled", "succeeded", "failed", "skipped"):
            return
        self.status = "cancelled"
        self.progress = None
        self.cancel_requested = True
        self.finished_at = _utcnow()
        self.updated_at = self.finished_at


class TaskTracker:
    """Process-local task registry.

    Single source of truth for both the upload endpoint (creates
    records) and the polling endpoint (reads them). All mutations
    go through a single ``threading.Lock`` so the polling handler
    never sees a torn write from the worker thread.
    """

    def __init__(self, db=None) -> None:
        self._db = db  # optional WebApiDB — durable task persistence (M3 batch 2)
        self._lock = threading.Lock()
        self._tasks: dict[UUID, TaskRecord] = {}
        # Latest task per (collection, source_path) — back-index for
        # ``DocumentDetail.last_task_id`` without scanning the registry.
        self._latest_by_doc: dict[tuple[str, str], UUID] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        document_id: UUID | None,
        collection_id: UUID,
        source_path: str,
        filename: str,
        task_id: UUID | None = None,
        task_type: str = "ingestion",
        attempt: int = 0,
        parent_task_id: UUID | None = None,
    ) -> TaskRecord:
        """Allocate a new task in the ``pending`` state and seed the back-index.

        ``task_id`` is optional — when omitted a fresh ``uuid4`` is
        generated. The caller may pass an id to make the upload folder
        name and the task id match (see :meth:`IngestionService.upload`).
        Document IDs stay the deterministic ``uuid5`` from
        ``mappers.document_uuid``; task IDs are random and unrelated.

        ``task_type`` distinguishes ingestion tasks (the default) from
        async query tasks (M3 batch 2) — query tasks carry no document
        and are not indexed in the ``(collection, source_path)`` back-index.

        ``attempt`` / ``parent_task_id`` (B3 retry) record how many times
        this task is a retry of a previously failed/cancelled ingestion and
        which task it restarted. ``parent_task_id`` is kept in-memory only.
        """
        with self._lock:
            record = TaskRecord(
                id=task_id or uuid4(),
                document_id=document_id,
                collection_id=collection_id,
                source_path=source_path,
                filename=filename,
                task_type=task_type,
                attempt=attempt,
                parent_task_id=parent_task_id,
            )
            self._tasks[record.id] = record
            # Track latest for the same (collection, source_path) — the
            # most recent upload wins. Earlier in-flight tasks remain
            # queryable by their own id.
            if task_type == "ingestion":
                self._latest_by_doc[(collection_id, source_path)] = record.id
            self._persist(record)
            return record

    def create_if_absent(
        self,
        *,
        task_id: UUID,
        document_id: UUID | None,
        collection_id: UUID,
        source_path: str,
        filename: str,
        task_type: str = "ingestion",
        attempt: int = 0,
        parent_task_id: UUID | None = None,
    ) -> TaskRecord:
        """Idempotently create a task with a caller-chosen ``task_id``.

        Used by retry so concurrent / duplicate retry requests never
        double-create a child task: the child's id is deterministic
        (parent id + attempt), so the second request returns the
        already-created child instead of spawning another.
        """
        with self._lock:
            existing = self._tasks.get(task_id)
            if existing is not None:
                return _snapshot(existing)
        return self.create(
            document_id=document_id,
            collection_id=collection_id,
            source_path=source_path,
            filename=filename,
            task_id=task_id,
            task_type=task_type,
            attempt=attempt,
            parent_task_id=parent_task_id,
        )

    def get(self, task_id: UUID) -> TaskRecord | None:
        """Snapshot read — returns the current record or ``None``.

        Falls back to the durable store when the task isn't in memory
        (e.g. created before a process restart).
        """
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is not None:
                return _snapshot(rec)
        if self._db is not None:
            row = self._db.get_task(str(task_id))
            if row is not None:
                return _record_from_row(row)
        return None

    def latest_for_document(
        self, collection_id: UUID, source_path: str,
    ) -> TaskRecord | None:
        """Most recent task for a (collection, source_path) pair."""
        with self._lock:
            tid = self._latest_by_doc.get((collection_id, source_path))
            if tid is not None:
                rec = self._tasks.get(tid)
                if rec is not None:
                    return _snapshot(rec)
        # Restart recovery: the memory back-index is empty but the
        # durable store still knows the last task for this document.
        if self._db is not None:
            row = self._db.latest_task_for(str(collection_id), source_path)
            if row is not None:
                return _record_from_row(row)
        return None

    # ------------------------------------------------------------------
    # Mutators — called only by the worker thread
    # ------------------------------------------------------------------

    def request_cancel(self, task_id: UUID) -> TaskRecord:
        """Record a cooperative-cancel request and return the snapshot.

        Idempotent: repeated cancel requests are no-ops. A ``pending`` task
        (queued for a worker slot) transitions straight to the terminal
        ``cancelled`` state; a ``running`` task keeps running but sets
        ``cancel_requested`` so the worker aborts at the next safe stage
        boundary. Terminal tasks are untouched (the caller raises the
        409 conflict).
        """
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None:
                raise KeyError(task_id)
            if rec.status in ("succeeded", "failed", "cancelled", "skipped"):
                return _snapshot(rec)
            rec.cancel_requested = True
            if rec.status == "pending":
                rec.mark_cancelled()
            self._persist(rec)
            return _snapshot(rec)

    def cancel_requested(self, task_id: UUID) -> bool:
        """Whether the worker should stop at the next boundary."""
        with self._lock:
            rec = self._tasks.get(task_id)
            return bool(rec and rec.cancel_requested)

    def update(self, task_id: UUID, mutator: "_TaskMutator") -> None:
        """Apply ``mutator(task)`` to the live record under the lock.

        Encapsulates the lock so callers don't have to know whether
        they're reading or writing. The mutator receives the live
        record (not a copy) and may call any of the
        ``TaskRecord.mark_*`` / ``update_progress`` helpers.
        """
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None:
                logger.warning("TaskTracker.update: task %s vanished", task_id)
                return
            mutator(rec)
            self._persist(rec)

    # ------------------------------------------------------------------
    # Introspection (test-only)
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    # ------------------------------------------------------------------
    # Durability (optional WebApiDB)
    # ------------------------------------------------------------------
    def _persist(self, rec: TaskRecord) -> None:
        """Best-effort write-through to the durable store."""
        if self._db is None:
            return
        try:
            self._db.upsert_task(_row_from_record(rec))
        except Exception:  # noqa: BLE001 — persistence must never break the flow
            logger.warning("failed to persist task %s", rec.id)


def _TaskMutator(rec: TaskRecord):  # pragma: no cover — protocol alias
    """Protocol for ``TaskTracker.update`` mutators — any callable
    taking a live ``TaskRecord`` and returning ``None``."""


def _row_from_record(rec: TaskRecord) -> dict:
    """Serialize a ``TaskRecord`` to the store's row shape."""
    return {
        "task_id": str(rec.id),
        "task_type": rec.task_type,
        "document_id": str(rec.document_id) if rec.document_id else None,
        "collection_id": str(rec.collection_id) if rec.collection_id else None,
        "source_path": rec.source_path,
        "filename": rec.filename,
        "status": rec.status,
        "progress_json": rec.progress.model_dump_json() if rec.progress else None,
        "error_json": rec.error.model_dump_json() if rec.error else None,
        "attempt": rec.attempt,
        "created_at": rec.created_at.timestamp(),
        "updated_at": rec.updated_at.timestamp(),
        "finished_at": rec.finished_at.timestamp() if rec.finished_at else None,
    }


def _record_from_row(row: dict) -> TaskRecord:
    """Rebuild a ``TaskRecord`` from a store row (restart recovery)."""
    return TaskRecord(
        id=UUID(row["task_id"]),
        task_type=row.get("task_type", "ingestion"),
        document_id=UUID(row["document_id"]) if row.get("document_id") else None,
        collection_id=UUID(row["collection_id"]) if row.get("collection_id") else None,
        source_path=row.get("source_path", ""),
        filename=row.get("filename", ""),
        status=row["status"],
        progress=TaskProgress.model_validate_json(row["progress_json"])
        if row.get("progress_json") else None,
        attempt=row.get("attempt", 0),
        error=TaskError.model_validate_json(row["error_json"])
        if row.get("error_json") else None,
        created_at=datetime.fromtimestamp(row["created_at"], tz=timezone.utc),
        updated_at=datetime.fromtimestamp(row["updated_at"], tz=timezone.utc),
        finished_at=datetime.fromtimestamp(row["finished_at"], tz=timezone.utc)
        if row.get("finished_at") else None,
    )


def _snapshot(rec: TaskRecord) -> TaskRecord:
    """Return a detached copy of ``rec`` with mutable fields re-built.

    The Pydantic-shaped fields (``progress``, ``error``) are themselves
    immutable from the caller's perspective, so aliasing them is fine.
    Dataclass equality / repr stay useful for tests.
    """
    return TaskRecord(
        id=rec.id,
        document_id=rec.document_id,
        collection_id=rec.collection_id,
        source_path=rec.source_path,
        filename=rec.filename,
        task_type=rec.task_type,
        status=rec.status,
        progress=rec.progress,
        attempt=rec.attempt,
        error=rec.error,
        parent_task_id=rec.parent_task_id,
        cancel_requested=rec.cancel_requested,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
        finished_at=rec.finished_at,
    )


__all__ = ["TaskRecord", "TaskTracker"]
