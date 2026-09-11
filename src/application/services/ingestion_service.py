"""
``IngestionService`` — application-layer entry point for document ingestion.

M1 职责（v0.1 契约）

- 薄封装，**不改变** ``IngestionPipeline.run`` 的行为。
- 给 CLI / MCP / Streamlit / Web API 四个入口一个稳定的依赖项。

M2 批次 2 扩展（upload → task）

- 持有内存级的 :class:`TaskTracker`，Web API 上传端点通过
  :meth:`upload` 提交任务，前端通过 :meth:`get_task` 轮询。
- ``upload(...)`` 同步触发摄入流程（在工作线程里跑），实现
  pending → running → succeeded/failed/skipped 状态机。
- M5 临时文件落地：上传字节流先写到**每任务唯一的临时路径**
  ``uploads/<collection>/.tmp/<task_id>-<filename>``，worker 从该临时
  文件摄取；文档身份仍是稳定 canonical 路径
  ``uploads/<collection>/<sanitised_filename>``（传给
  ``IngestionPipeline.run(source_path=...)``）。解析成功后以原子替换写入该
  canonical 路径供管理台保真预览；document_id 保持稳定。
- 进度通过 :class:`IngestionPipeline` 的 ``on_progress`` 回调，转成
  :class:`TaskProgress` 快照。

后续会扩展

- 写入 SQLite 持久化（v0.2，让任务可跨进程查询）
- 异步执行 / 取消 / 重试（M3）
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import UUID, uuid4, uuid5

from src.application.identifiers import collection_uuid, document_uuid
from src.application.services.task_tracker import TaskRecord, TaskTracker
from src.application.services.task_types import TaskError, TaskStage
from src.application.services.upload_types import (
    BatchFileResult,
    BatchFileUpload,
    BatchUploadResponse,
    UploadPolicy,
)
from src.ingestion.storage.bm25_locks import bm25_write_lock
from src.libs.embedding.usage import embedding_usage_context

if TYPE_CHECKING:
    from src.application.services.trace_store import TraceStore
    from src.core.trace.trace_context import TraceContext
    from src.ingestion.pipeline import IngestionPipeline, PipelineResult

logger = logging.getLogger(__name__)


class TaskCancelledError(Exception):
    """Raised inside the worker when a cooperative-cancel request is seen.

    Signal-only; the worker catches it, marks the task ``cancelled`` and
    records a terminal ``canceled`` trace without rolling back any already
    completed atomic writes (existing documents / old indexes are kept).
    """


class RetrySourceMissingError(LookupError):
    """Raised by :meth:`IngestionService.retry` when the original source
    file is no longer present on disk — surfaced as an actionable 404."""


# A retry child's id is a deterministic uuid5(parent-task-id, attempt-tag),
# so duplicate or concurrent retries of the same task collide to the SAME
# child id, which ``TaskTracker.create_if_absent`` turns into a no-op.
_RETRY_TAG = "skdy-retry"


# Stage names the pipeline emits (it currently emits "load", "split",
# "transform:<name>", "embed", "upsert"). We canonicalise "transform:<x>"
# to plain "transform" so the frontend doesn't have to handle a
# different stage per transform. The pipeline-level "transform:<x>"
# granularity is preserved in the trace endpoint (batch 3).
_PIPELINE_STAGE_TO_PUBLIC: dict[str, TaskStage] = {
    "load": "load",
    "split": "split",
    "embed": "embed",
    "upsert": "upsert",
    # Anything starting with "transform:" gets mapped to "transform".
}


def _canonical_stage(stage: str) -> TaskStage:
    """Map a pipeline-level stage name to the v0.1 contract enum."""
    if stage in _PIPELINE_STAGE_TO_PUBLIC:
        return _PIPELINE_STAGE_TO_PUBLIC[stage]  # type: ignore[return-value]
    if stage.startswith("transform:"):
        return "transform"
    # Unknown stage — fall back to the most general one. The contract
    # currently bans unknown stages (see ``TaskStage`` literals), but we
    # keep ingestion alive if the pipeline ever adds a new one.
    logger.warning("unknown pipeline stage %r — mapping to 'transform'", stage)
    return "transform"


def _percent_from_counts(current: int, total: int) -> int:
    """Compute a 0–100 progress percentage from raw counts.

    Mirrors the rule ``min(100, max(0, round(current / total * 100)))``
    and handles ``total <= 0`` defensively (returns 0). The pipeline's
    contract says callers may pass ``total=0`` for stages that don't
    know their denominator, so this helper is intentionally forgiving.
    """
    if total <= 0:
        return 0
    pct = int(round(current / total * 100))
    return max(0, min(100, pct))


class IngestionService:
    """Facade over :class:`IngestionPipeline` with task tracking.

    Owns a :class:`TaskTracker` that survives for the lifetime of the
    application services (one per boot). The :class:`IngestionPipeline`
    is shared with the CLI / MCP / Streamlit boot paths so all four
    entry points exercise the same code.

    ``engines`` is duck-typed:

    - a single :class:`IngestionPipeline` (CLI / MCP / tests) — every
      run uses it regardless of ``collection``;
    - an :class:`EngineCache` (Web API, M3) — :meth:`upload` routes
      ``collection`` to the per-collection pipeline via
      ``EngineCache.pipeline_for``, so uploads write to the right
      vector / BM25 / image / integrity row per collection.
    """

    def __init__(
        self,
        engines: "IngestionPipeline | Any",
        *,
        upload_dir: str | Path | None = None,
        trace_store: "TraceStore | None" = None,
        tracker: TaskTracker | None = None,
        upload_policy: UploadPolicy | None = None,
        on_ingested: Callable[[], None] | None = None,
    ) -> None:
        self._engines = engines
        # Web API boot injects one shared (SQLite-backed) TaskTracker so
        # query tasks and ingestion tasks live in the same registry.
        self._tracker = tracker if tracker is not None else TaskTracker()
        self._upload_dir = Path(upload_dir) if upload_dir else Path("./data/uploads")
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._trace_store = trace_store
        # Upload limits come from the injected policy (Web API builds it
        # from its env-driven settings) — the application layer never
        # imports the Web API layer. Defaults mirror the historical values.
        self._policy = upload_policy if upload_policy is not None else UploadPolicy()
        # Fired after a background task successfully ingests a NEW document
        # (not on dedup-skip). The Web API composes this to invalidate the
        # collection-stats + document-UUID caches that live on
        # ``DocumentService``, so a doc is resolvable the moment the
        # pipeline finishes writing its integrity record.
        self._on_ingested: Callable[[], None] | None = on_ingested
        # Bound concurrent ingestion pipelines so large batches do not
        # overwhelm Embedding, Chroma, and the per-collection BM25 lock.
        self._worker_slots = threading.BoundedSemaphore(4)
        self._queued_workers: dict[UUID, tuple[str, str, str]] = {}
        self._queued_workers_lock = threading.Lock()
        # Per-task post-completion hooks. Production leaves the dict
        # empty; tests register a hook to clean up temp files. Keys are
        # mutation-safe because the lock is owned by the tracker.
        self._pending_hooks: dict[UUID, Callable[[TaskRecord], None]] = {}
        # M5: process-local batch registry (submission-time snapshots).
        # Not durable — the tasks themselves persist in WebApiDB; the
        # batch_id simply correlates the tasks created in one request.
        self._batches: dict[UUID, BatchUploadResponse] = {}

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    @property
    def pipeline(self) -> "IngestionPipeline":
        """The single ``IngestionPipeline`` (legacy single-pipeline construction).

        Raises ``TypeError`` when this service was built with an
        :class:`EngineCache` — use :meth:`upload` with ``collection=``
        to route there.
        """
        if hasattr(self._engines, "pipeline_for"):
            raise TypeError(
                "IngestionService was built with an EngineCache; "
                "route via upload(collection=...).",
            )
        return self._engines

    def _pipeline_for(self, collection: str) -> "IngestionPipeline":
        """Resolve the pipeline serving ``collection``.

        A single-pipeline service ignores ``collection`` and returns the
        one pipeline; a cache-backed service dispatches per collection.
        """
        if hasattr(self._engines, "pipeline_for"):
            return self._engines.pipeline_for(collection)
        return self._engines

    @property
    def tracker(self) -> TaskTracker:
        return self._tracker

    @property
    def upload_dir(self) -> Path:
        return self._upload_dir

    @property
    def trace_store(self) -> "TraceStore | None":
        return self._trace_store

    # ------------------------------------------------------------------
    # Legacy M1 entry point — CLI / MCP / Streamlit keep calling this.
    # ------------------------------------------------------------------
    def ingest(
        self,
        path: str,
        *,
        on_progress: Callable[[str, int, int], None] | None = None,
        trace: "TraceContext | None" = None,
    ) -> "PipelineResult":
        """Ingest a single file end-to-end (sync, no task tracking).

        Legacy entry point (CLI / MCP / Streamlit) — the pipeline is
        already bound to a collection at build time, so the default
        ``collection`` is used for the ``EngineCache`` construction
        (Web API callers use :meth:`upload` instead).
        """
        return self._pipeline_for("default").run(
            path=path, on_progress=on_progress, trace=trace,
        )

    # ------------------------------------------------------------------
    # M2 batch 2 — Web API upload entry point
    # ------------------------------------------------------------------
    def compute_source_path(self, collection: str, filename: str) -> Path:
        """Compute the canonical on-disk path for an upload.

        Layout: ``upload_dir/<collection>/<sanitised_filename>``. The
        path is stable across re-uploads of the same file to the same
        collection, so the document id — uuid5 of ``(collection,
        source_path)`` — is also stable. The router uses this same
        path for id derivation before calling :meth:`upload`.

        The file is **not** written here; only the path is computed.
        """
        safe = self._sanitise_filename(filename or "upload.bin")
        return self._upload_dir / (collection or "default") / safe

    def upload(
        self,
        *,
        bytes_payload: bytes,
        filename: str,
        collection: str,
        collection_id: UUID,
        document_id: UUID,
        source_path: Path | str | None = None,
        on_complete: Callable[[TaskRecord], None] | None = None,
    ) -> TaskRecord:
        """Accept an upload, kick off ingestion, return the task ID.

        ``bytes_payload`` is the raw file body; ``filename`` is the
        original name (used for diagnostics + the file on disk).
        ``collection`` / ``collection_id`` / ``document_id`` identify
        the target resource. ``source_path`` is the on-disk path the
        pipeline will ingest — pass it from :meth:`compute_source_path`
        so the document id and the file stay aligned. When omitted,
        the service computes a path of the same shape internally.

        The pipeline runs synchronously *in the calling thread*; the
        HTTP layer is expected to call this from a background thread
        (e.g. ``BackgroundTasks``) if it wants the request to return
        before ingestion finishes. The task starts in ``pending``,
        transitions to ``running`` once the pipeline's first stage
        fires, and ends in ``succeeded`` / ``failed``.

        ``on_complete`` is invoked once with the final ``TaskRecord``
        snapshot, regardless of outcome. Use it for cleanup (e.g.
        removing the temp file). It's called from the worker thread,
        so keep it cheap and thread-safe.
        """
        return self._submit_file(
            bytes_payload=bytes_payload,
            filename=filename,
            collection=collection,
            collection_id=collection_id,
            document_id=document_id,
            source_path=source_path,
            on_complete=on_complete,
        )

    def _submit_file(
        self,
        *,
        bytes_payload: bytes,
        filename: str,
        collection: str,
        collection_id: UUID,
        document_id: UUID,
        source_path: Path | str | None = None,
        on_complete: Callable[[TaskRecord], None] | None = None,
        defer_worker: bool = False,
        task_id: UUID | None = None,
        attempt: int = 0,
        parent_task_id: UUID | None = None,
    ) -> TaskRecord:
        """Stage the upload bytes, create a task and spawn the worker.

        Shared by the single-file :meth:`upload` and the batch path so
        both exercise the same stage → task → worker sequence. See
        :meth:`upload` for the full contract.

        M5 concurrency fix: the bytes are written to a **per-task unique
        temp path** (``upload_dir/<collection>/.tmp/<task_id>-<filename>``),
        rather than directly to the stable canonical path. Two concurrent batches
        uploading the same filename with different content therefore
        cannot overwrite each other's in-flight file. The worker ingests
        from the temp file, labels everything with the stable canonical
        ``source_path`` (document identity unchanged), then atomically promotes
        the successful upload to that canonical path for original-file preview.

        Retry (B3.3): when ``task_id`` is provided (a deterministic retry
        child id), the task is created idempotently via
        :meth:`TaskTracker.create_if_absent` so a concurrent / duplicate
        retry of the same parent never double-creates a child. ``attempt``
        and ``parent_task_id`` are stamped onto the record.
        """
        # ---- 1. Resolve the canonical source path --------------------
        # ``source_path`` is the document identity (uuid5 input); it is
        # It keeps the original filename suffix so the temp file below
        # dispatches the right loader and becomes the persisted preview path.
        if source_path is None:
            source_path = self.compute_source_path(collection, filename)
        canonical = Path(source_path)

        # ---- 2. Stage the bytes at a unique temp path ---------------
        task_id = task_id or uuid4()
        temp_dir = self._upload_dir / (collection or "default") / ".tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        ingest_path = temp_dir / f"{task_id}-{self._sanitise_filename(filename)}"
        ingest_path.write_bytes(bytes_payload)

        # ---- 3. Seed the task record (source_path = canonical) ------
        if parent_task_id is not None:
            record = self._tracker.create_if_absent(
                task_id=task_id,
                document_id=document_id,
                collection_id=collection_id,
                source_path=str(canonical),
                filename=filename,
                attempt=attempt,
                parent_task_id=parent_task_id,
            )
        else:
            record = self._tracker.create(
                task_id=task_id,
                document_id=document_id,
                collection_id=collection_id,
                source_path=str(canonical),
                filename=filename,
            )
        if on_complete is not None:
            self._pending_hooks[task_id] = on_complete

        # ---- 4. Spawn the worker thread -----------------------------
        # ``daemon=True`` so a hard kill during ingest doesn't leave a
        # zombie thread blocking process exit. The collection name is
        # passed along so a cache-backed service routes this task to the
        # right per-collection pipeline (M3).
        with self._queued_workers_lock:
            self._queued_workers[task_id] = (str(ingest_path), str(canonical), collection)
        if not defer_worker:
            self.start_worker(task_id)
        return record

    def start_worker(self, task_id: UUID) -> None:
        """Start one staged ingestion task; safe to call only once."""
        with self._queued_workers_lock:
            args = self._queued_workers.pop(task_id, None)
        if args is None:
            return
        worker = threading.Thread(
            target=self._run_worker,
            args=(task_id, *args),
            name=f"ingest-{task_id}",
            daemon=True,
        )
        worker.start()

    def start_batch(self, batch_id: UUID) -> None:
        """Start accepted tasks after the HTTP 202 response is sent."""
        batch = self._batches.get(batch_id)
        if batch is None:
            return
        for item in batch.files:
            if item.task_id is not None:
                self.start_worker(item.task_id)

    # ------------------------------------------------------------------
    # M5 — batch ingestion
    # ------------------------------------------------------------------
    def upload_batch(
        self,
        *,
        items: list[BatchFileUpload],
        collection: str,
        collection_id: UUID,
        on_complete: Callable[[TaskRecord], None] | None = None,
        defer_workers: bool = False,
    ) -> BatchUploadResponse:
        """Submit a batch of files to ``collection``; each gets its own task.

        Per-file semantics (M5):

        - **Validation is independent.** An unsupported extension /
          MIME / oversized / duplicate-named file becomes a ``rejected``
          entry with a structured ``error`` — it never blocks the other
          files and never touches disk.
        - **Dedup is pre-checked.** A file whose SHA256 is already marked
          ``success`` for this collection becomes a ``skipped`` entry with
          no task and no worker. Same-batch byte-identical copies are both
          reported ``accepted``: the per-collection write lock serializes
          their workers, so the second one ends ``skipped`` (and correctly
          re-runs if the first one *fails*).
        - **Each accepted file** is written to ``upload_dir/<collection>/``
          and spawned through the same worker path as a single upload, so
          it gets its own task record + trace.
        """
        batch_id = uuid4()
        results: list[BatchFileResult] = []
        seen_source_paths: set[str] = set()
        # Resolve the pipeline once for the dedup pre-check (the worker
        # re-resolves it under the write lock — same engine).
        pipeline = self._pipeline_for(collection)
        integrity = getattr(pipeline, "file_integrity", None)

        for item in items:
            result = self._submit_batch_file(
                item=item,
                collection=collection,
                collection_id=collection_id,
                batch_id=batch_id,
                pipeline=pipeline,
                integrity=integrity,
                seen_source_paths=seen_source_paths,
                on_complete=on_complete,
                defer_worker=defer_workers,
            )
            results.append(result)

        accepted = sum(1 for r in results if r.status == "accepted")
        skipped = sum(1 for r in results if r.status == "skipped")
        rejected = sum(1 for r in results if r.status == "rejected")
        response = BatchUploadResponse(
            batch_id=batch_id,
            collection_id=collection_id,
            total=len(results),
            accepted=accepted,
            skipped=skipped,
            rejected=rejected,
            files=results,
        )
        self._batches[batch_id] = response
        return response

    def _submit_batch_file(
        self,
        *,
        item: BatchFileUpload,
        collection: str,
        collection_id: UUID,
        batch_id: UUID,
        pipeline: Any,
        integrity: Any,
        seen_source_paths: set[str],
        on_complete: Callable[[TaskRecord], None] | None,
        defer_worker: bool,
    ) -> BatchFileResult:
        """Validate + submit one batch file; return its per-file result."""
        # --- 1. Boundary validation (never touches disk) --------------
        source_path = self.compute_source_path(collection, item.filename)
        ext = Path(item.filename).suffix.lower()
        if ext not in self._policy.allowed_extensions:
            return self._rejected_batch_file(
                item, "UNSUPPORTED_FILE_TYPE",
                f"Unsupported file extension {ext!r}; allowed: "
                f"{sorted(self._policy.allowed_extensions)}",
            )
        if item.content_type not in self._policy.allowed_mime:
            return self._rejected_batch_file(
                item, "UNSUPPORTED_MEDIA_TYPE",
                f"File content-type {item.content_type!r} is not allowed; "
                f"expected one of {sorted(self._policy.allowed_mime)}",
            )
        if item.size_bytes > self._policy.max_file_bytes:
            return self._rejected_batch_file(
                item, "FILE_TOO_LARGE",
                f"file size {item.size_bytes} bytes exceeds the "
                f"{self._policy.max_file_bytes}-byte limit",
            )
        # Two names that sanitise to the same on-disk path would collide on
        # the canonical document identity — reject the later duplicate so a
        # single batch never maps two files to one document.
        if str(source_path) in seen_source_paths:
            return self._rejected_batch_file(
                item, "DUPLICATE_FILENAME",
                f"filename {item.filename!r} collides with an earlier file "
                f"in this batch (same destination path)",
            )
        seen_source_paths.add(str(source_path))

        # --- 2. Dedup pre-check ---------------------------------------
        if integrity is not None:
            file_hash = hashlib.sha256(item.bytes_payload).hexdigest()
            if integrity.should_skip(file_hash, collection=collection):
                return BatchFileResult(
                    filename=item.filename,
                    document_id=document_uuid(collection, str(source_path)),
                    task_id=None,
                    status="skipped",
                    size_bytes=item.size_bytes,
                    error=None,
                )

        # --- 3. Submit the task ---------------------------------------
        doc_id = document_uuid(collection, str(source_path))
        record = self._submit_file(
            bytes_payload=item.bytes_payload,
            filename=item.filename,
            collection=collection,
            collection_id=collection_id,
            document_id=doc_id,
            source_path=source_path,
            on_complete=on_complete,
            defer_worker=defer_worker,
        )
        return BatchFileResult(
            filename=item.filename,
            document_id=doc_id,
            task_id=record.id,
            status="accepted",
            size_bytes=item.size_bytes,
            error=None,
        )

    @staticmethod
    def _rejected_batch_file(
        item: BatchFileUpload, code: str, message: str,
    ) -> BatchFileResult:
        """A validation-rejected file — no document, no task, no disk write."""
        return BatchFileResult(
            filename=item.filename,
            document_id=None,
            task_id=None,
            status="rejected",
            size_bytes=item.size_bytes,
            error=TaskError(code=code, message=message, details={}),
        )

    def get_batch(self, batch_id: UUID) -> BatchUploadResponse | None:
        """Snapshot of a batch's submission-time results (process-local).

        Not durable: the individual tasks persist in the WebApiDB-backed
        tracker, so after a restart the ``batch_id`` no longer resolves.
        The frontend should rely on the per-file ``task_id``s, which stay
        queryable.
        """
        return self._batches.get(batch_id)

    # ------------------------------------------------------------------
    # M6 / task book B2.8 — reprocess an existing document
    # ------------------------------------------------------------------
    def reprocess(
        self,
        *,
        collection: str,
        collection_id: UUID,
        document_id: UUID,
        source_path: str | Path,
        filename: str,
    ) -> TaskRecord:
        """Re-run ingestion on an existing document's canonical file.

        Reuses the upload staging machinery so a reprocess task walks the
        exact same ``pending → running → succeeded/failed/skipped`` path as
        an upload: the existing file is read, staged to a per-task temp
        path, ingested, and atomically promoted back to the stable
        canonical path. Each invocation returns a fresh, independent
        :class:`TaskRecord`.

        Duplicate-enqueue protection lives in the caller (the batch router
        checks task state before enqueueing) so this method assumes it is
        safe to start a new task here. If the on-disk file is missing we
        let :class:`FileNotFoundError` propagate — the HTTP layer maps it
        to a per-item error rather than uploading nothing.
        """
        canonical = Path(source_path)
        payload = canonical.read_bytes()
        return self._submit_file(
            bytes_payload=payload,
            filename=filename,
            collection=collection,
            collection_id=collection_id,
            document_id=document_id,
            source_path=canonical,
        )

    # ------------------------------------------------------------------
    # B3.3 — retry a failed / cancelled ingestion
    # ------------------------------------------------------------------
    def retry(self, task_id: UUID) -> TaskRecord | None:
        """Re-create a failed/cancelled ingestion as a fresh task + trace.

        Only ``failed`` / ``cancelled`` sources may be retried (the router
        rejects everything else with a stable 409). The original task and
        its trace are left intact; a NEW task and trace are created with
        ``attempt == parent.attempt + 1`` and ``parent_task_id`` pointing
        back at the original, re-using the original document source file
        and parsing config (same collection / collection_id / document_id,
        same canonical path / filename).

        Idempotency: the child task's id is a deterministic
        ``uuid5(parent_task_id, attempt)``, so a repeated identical retry
        of the same parent returns the already-created child instead of
        spawning another task.

        If the original source file is gone from disk, raises
        :class:`RetrySourceMissingError` — the HTTP layer maps it to an
        actionable 404.
        """
        original = self._tracker.get(task_id)
        if original is None or original.status not in ("failed", "cancelled"):
            return None
        attempt = original.attempt + 1
        child_id = uuid5(UUID(str(task_id)), f"{_RETRY_TAG}:{attempt}")
        existing = self._tracker.get(child_id)
        if existing is not None:
            return existing  # duplicate / concurrent retry — already created
        canonical = Path(original.source_path)
        if not canonical.is_file():
            raise RetrySourceMissingError(str(canonical))
        # The canonical layout is ``upload_dir/<collection>/<filename>``
        # (see :meth:`compute_source_path`), so the parent directory names
        # the collection we route the retry to.
        parent = canonical.parent.name
        collection = parent if parent and parent not in {"", "."} else "default"
        payload = canonical.read_bytes()
        return self._submit_file(
            bytes_payload=payload,
            filename=original.filename,
            collection=collection,
            collection_id=original.collection_id,
            document_id=original.document_id,
            source_path=canonical,
            task_id=child_id,
            attempt=attempt,
            parent_task_id=task_id,
        )

    # ------------------------------------------------------------------
    # B3.4 — cooperative cancel of a pending / running ingestion
    # ------------------------------------------------------------------
    def cancel(self, task_id: UUID) -> TaskRecord | None:
        """Record a cooperative-cancel request and return the snapshot.

        Idempotent: repeated cancels are no-ops. A ``pending`` task is
        cancelled immediately; a ``running`` task keeps running so the
        worker can stop at the next safe stage boundary and mark it
        ``cancelled``. Terminal tasks are left untouched — the router maps
        that to a stable 409.
        """
        try:
            return self._tracker.request_cancel(task_id)
        except KeyError:
            # Task isn't in the in-memory registry (e.g. after a restart);
            # return whatever snapshot exists (the router handles 404).
            return self._tracker.get(task_id)

    # ------------------------------------------------------------------
    # Public task lookup
    # ------------------------------------------------------------------
    def get_task(self, task_id: UUID) -> TaskRecord | None:
        """Snapshot read of a task by id (the polling endpoint)."""
        return self._tracker.get(task_id)

    def get_latest_task_for(
        self, collection_id: UUID, source_path: str,
    ) -> TaskRecord | None:
        """Latest task for a (collection, source_path) pair.

        Used by the document-detail endpoint to surface ``last_task_id``.
        Returns ``None`` when the document has never been uploaded.
        """
        return self._tracker.latest_for_document(collection_id, source_path)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _run_worker(
        self,
        task_id: UUID,
        ingest_path: str,
        canonical_source: str,
        collection: str = "default",
    ) -> None:
        """Bounded worker entry point. Large batches queue after four active pipelines.
        """
        with self._worker_slots:
            self._run_worker_unbounded(task_id, ingest_path, canonical_source, collection)

    def _run_worker_unbounded(
        self,
        task_id: UUID,
        ingest_path: str,
        canonical_source: str,
        collection: str = "default",
    ) -> None:
        """Thread body: drive the pipeline and update the task record.

        ``ingest_path`` is the unique per-task temp file the pipeline
        reads; ``canonical_source`` is the stable document identity
        passed to ``run(source_path=...)``. ``collection`` selects the
        per-collection pipeline when this service was built with an
        :class:`EngineCache`; the single pipeline construction ignores it.
        The temp file is removed when the task reaches a terminal state.
        """
        # First transition: pending → running. We mark stage="load" so
        # the very first poll shows motion instead of a stuck "pending".
        self._tracker.update(
            task_id,
            lambda rec: rec.mark_running("load"),
        )

        # Guard against a cancel that queued while this task waited for a
        # worker slot: the task is already terminal ``cancelled`` → clean up
        # and bail before running any pipeline work.
        if self._tracker.cancel_requested(task_id):
            self._tracker.update(task_id, lambda rec: rec.mark_cancelled())
            self._cleanup_temp(ingest_path)
            self._invoke_on_complete(task_id)
            return

        # M2 batch 3: when a trace store is wired (Web API boot), the
        # ingestion run records a trace whose id == task id, so
        # ``GET /ingestions/{id}/trace`` works. CLI / MCP keep their
        # own trace handling (trace_store is None there).
        trace = None
        if self._trace_store is not None:
            from src.core.trace.trace_context import (
                TRACE_TYPE_INGESTION,
                TraceContext,
            )

            rec = self._tracker.get(task_id)
            trace = TraceContext(
                trace_id=str(task_id),
                trace_type=TRACE_TYPE_INGESTION,
                metadata={
                    "collection_id": str(rec.collection_id) if rec else None,
                    "document_id": str(rec.document_id) if rec and rec.document_id else None,
                    "collection": collection,
                    "source_path": canonical_source,
                    "filename": Path(canonical_source).name,
                },
            )
            if rec is not None:
                trace.attempt = rec.attempt
                trace.parent_trace_id = (
                    str(rec.parent_task_id) if rec.parent_task_id else None
                )

        def on_progress(stage: str, current: int, total: int) -> None:
            """Bridge pipeline progress → TaskProgress + live trace.

            Also the cooperative-cancel checkpoint: if a cancel request has
            landed, raise so the pipeline aborts *after* its currently
            running stage's atomic write completes — we never roll back
            already-committed work, we just stop the remaining stages.
            """
            if self._tracker.cancel_requested(task_id):
                raise TaskCancelledError(task_id)
            public_stage = _canonical_stage(stage)
            percent = _percent_from_counts(current, total)
            self._tracker.update(
                task_id,
                lambda rec: rec.update_progress(
                    public_stage, current, total, percent,
                ),
            )
            # Keep the in-memory trace fresh so ``GET /ingestions/{id}/trace``
            # can report completed stages + the current stage mid-flight.
            if self._trace_store is not None and trace is not None:
                self._trace_store.upsert_live(trace)

        # The whole write sequence — pipeline run (which rewrites the
        # collection's on-disk BM25 index) + cache invalidation — runs
        # under the per-collection write lock so concurrent uploads /
        # deletes to the same collection serialize (RLock re-enters when
        # the pipeline re-acquires it for the index write).
        with bm25_write_lock(collection):
            try:
                # PRD §5.2: tag the pipeline's dense-embedding calls as
                # ``ingestion`` (the SparseEncoder uses BM25 char n-grams,
                # so only dense calls emit usage). ContextVar set + read in
                # this worker thread, so async uploads tag correctly too.
                # The task record carries the business ids.
                rec = self._tracker.get(task_id)
                with embedding_usage_context(
                    operation="ingestion",
                    collection_id=str(rec.collection_id) if rec else None,
                    trace_id=str(task_id),
                    task_id=str(task_id),
                    document_id=str(rec.document_id) if rec and rec.document_id else None,
                ):
                    # M3: pass ``collection=`` explicitly so the pipeline's
                    # per-run scoping (integrity + image writes) matches the
                    # collection this task was routed to — the pipeline's
                    # ctor ``collection`` default is "default" and would
                    # otherwise mislabel non-default uploads.
                    # M5: read the unique temp file but label the document
                    # with the stable canonical source_path.
                    result = self._pipeline_for(collection).run(
                        path=ingest_path, on_progress=on_progress, trace=trace,
                        collection=collection, source_path=canonical_source,
                    )
                    # Preserve the original upload for faithful management-UI
                    # preview. The collection lock serializes same-name writes;
                    # os.replace prevents readers from observing partial bytes.
                    canonical = Path(canonical_source)
                    canonical.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(ingest_path, canonical)
            except TaskCancelledError:
                # Cooperative cancel: stop here, do not roll back completed
                # writes, mark the task terminal ``cancelled`` and record a
                # terminal ``canceled`` trace.
                self._tracker.update(task_id, lambda rec: rec.mark_cancelled())
                if trace is not None:
                    trace.record_stage(
                        "ingestion_pipeline", event="cancelled",
                    )
                    trace.status = "canceled"
                    trace.finish()
                    self._trace_store.record(trace)  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001 — any failure → task failed
                self._record_failure(task_id, exc)
                if trace is not None:
                    trace.status = "failed"
                    trace.finish()
                    self._trace_store.record(trace)  # type: ignore[union-attr]
            else:
                # M5: a pipeline run that short-circuited because the
                # file's hash is already marked success for this
                # collection is a *skip*, not a success. ``getattr``
                # guards fakes that return ``None`` from ``run()``.
                if getattr(result, "skipped", False):
                    if trace is not None:
                        trace.status = "skipped"
                    self._tracker.update(
                        task_id,
                        lambda rec: rec.mark_skipped(),
                    )
                else:
                    if trace is not None:
                        trace.status = "success"
                    self._tracker.update(
                        task_id,
                        lambda rec: rec.mark_succeeded(),
                    )
                    # A NEW document was written (integrity record included).
                    # Invalidate caches now so it is immediately resolvable.
                    if self._on_ingested is not None:
                        self._on_ingested()
                if trace is not None:
                    trace.finish()
                    self._trace_store.record(trace)  # type: ignore[union-attr]
            finally:
                # Drop the cached engines while still holding the write
                # lock so the SparseRetriever reloads the fresh index on
                # the next query (M3 consistency loop).
                self._invalidate_collection(collection)
        # M5: the staged temp file is no longer needed — remove it whether
        # the task succeeded, failed or was skipped.
        self._cleanup_temp(ingest_path)
        # Post-completion hook runs outside the lock (may be slow; the
        # task is already terminal by now).
        self._invoke_on_complete(task_id)

    @staticmethod
    def _cleanup_temp(ingest_path: str) -> None:
        """Best-effort removal of a staged temp upload file."""
        try:
            Path(ingest_path).unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to clean up staged upload %s: %s", ingest_path, exc)

    def _invalidate_collection(self, collection: str) -> None:
        """Tell the ``EngineCache`` to drop the collection's cached engines.

        The ingestion run changed the on-disk BM25 index; invalidating
        forces the next query to rebuild the ``HybridSearch`` and reload
        the fresh index. No-op for the single-pipeline construction
        (CLI / MCP / tests that don't wire an EngineCache).
        """
        invalidate = getattr(self._engines, "invalidate_collection", None)
        if callable(invalidate):
            invalidate(collection)

    def _record_failure(self, task_id: UUID, exc: Exception) -> None:
        """Convert an exception into a structured ``TaskError`` + status flip."""
        # We reuse the same code vocabulary as the top-level HTTP error
        # envelope so the frontend branches consistently. Storage /
        # provider failures get ``UPSTREAM_ERROR``; everything else is
        # ``INTERNAL_ERROR`` (the boundary never speaks the raw
        # exception message to the client).
        from src.ingestion.pipeline import PipelineStageError

        if isinstance(exc, PipelineStageError):
            code = "UPSTREAM_ERROR"
            message = f"ingestion failed at stage {exc.stage}"
            details: dict[str, object] = {"stage": exc.stage}
        else:
            code = "INTERNAL_ERROR"
            message = "ingestion failed"
            details = {}
        details["exception_type"] = type(exc).__name__
        task_error = TaskError(code=code, message=message, details=details)

        def _flip(rec: TaskRecord) -> None:
            rec.mark_failed(task_error)

        self._tracker.update(task_id, _flip)
        logger.warning("task %s failed: %s", task_id, exc)

    def _invoke_on_complete(self, task_id: UUID) -> None:
        """Run the post-task hook (cleanup) under the lock."""
        hook = self._pending_hooks.pop(task_id, None)
        if hook is None:
            return
        snapshot = self._tracker.get(task_id)
        if snapshot is None:
            return
        try:
            hook(snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.warning("on_complete hook failed: %s", exc)

    @staticmethod
    def _sanitise_filename(name: str) -> str:
        """Strip path separators and replays-injection-y characters.

        The filename is only used as the on-disk label inside the
        upload folder (no shell, no URL path), so we keep the rule
        minimal: no slashes, no leading dots, no empty.
        """
        cleaned = Path(name).name.replace("/", "_").replace("\\", "_")
        if cleaned in {"", ".", ".."}:
            cleaned = "upload.bin"
        if len(cleaned) > 200:
            stem, _, suffix = cleaned.rpartition(".")
            cleaned = (stem[:200 - len(suffix) - 1] if suffix else cleaned[:200])
        return cleaned


__all__ = ["BatchFileUpload", "IngestionService"]
