"""
IngestionPipeline (C14) — end-to-end orchestrator for a single document.

Wires together every stage built in C2–C13:

```
   ┌────────────────┐
   │ FileIntegrity  │  skip if already ingested
   └───────┬────────┘
           ▼
   ┌────────────────┐
   │   BaseLoader   │  PDF / DOCX / ... → Document
   └───────┬────────┘
           ▼
   ┌────────────────┐
   │ DocumentChunker│  Document → list[Chunk]
   └───────┬────────┘
           ▼
   ┌────────────────┐
   │ [BaseTransform]│  ChunkRefiner → MetadataEnricher → ...
   └───────┬────────┘
           ▼
   ┌────────────────┐
   │ BatchProcessor │  dense + sparse vectors
   └───────┬────────┘
           ▼
   ┌────────────────┐
   │  VectorUpserter│  → BaseVectorStore
   │  BM25Indexer   │  → ./data/db/bm25/
   │  ImageStorage  │  → ./data/images/
   └────────────────┘
```

Failures are reported per-stage with clear context — a transform
crash never aborts the whole pipeline, it just marks the chunk
``refined_by="error"`` (already handled in C5) and moves on.
Loader/encoder/storage failures are fatal (no fallback) and
re-raise with the stage name in the exception message.

Tracing (F4)
------------
When a ``TraceContext`` is passed to :meth:`run`, five
orchestrator-level stage events are recorded on it:

- ``load``      — file → Document (single event, ``method`` = loader class)
- ``split``     — Document → list[Chunk] (single event, ``method`` = chunker class)
- ``transform`` — one event per transform (``method`` = each transform's ``name``)
- ``embed``     — BatchProcessor (single event, ``method`` = batch processor class)
- ``upsert``    — VectorUpserter + BM25Indexer (single event, ``method`` = ``"vector+bm25"``)

Each event carries ``elapsed_ms`` and a stage-appropriate
``method`` tag; skipped runs and per-stage errors emit their
own ``event="skipped"`` / ``event="error"`` records so downstream
readers can still group by stage name.
"""

from __future__ import annotations

import logging
import time

import base64
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from src.core.types import Chunk, ChunkRecord, Document
from src.ingestion.storage.bm25_locks import bm25_write_lock
from src.libs.loader.base_loader import BaseLoader
from src.libs.loader.file_integrity import FileIntegrityChecker

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext
    from src.ingestion.chunking.document_chunker import DocumentChunker
    from src.ingestion.embedding.batch_processor import BatchProcessor
    from src.ingestion.storage.bm25_indexer import BM25Indexer
    from src.ingestion.storage.image_storage import ImageStorage
    from src.ingestion.storage.vector_upserter import VectorUpserter
    from src.ingestion.transform.base_transform import BaseTransform


logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Summary of one pipeline run, returned to the caller."""
    skipped: bool = False
    file_hash: str = ""
    source_path: str = ""
    n_chunks: int = 0
    n_records_upserted: int = 0
    bm25_n_docs: int = 0
    n_images_saved: int = 0
    errors: list[str] = field(default_factory=list)


class PipelineStageError(RuntimeError):
    """
    Raised when a non-recoverable stage fails.

    The ``stage`` attribute names the failing step so the caller can
    log it without parsing the message.
    """

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"[{stage}] {message}")
        self.stage = stage


class IngestionPipeline:
    """
    Orchestrate the full ingestion chain for a single file.

    Required collaborators (inject for testability):

    - ``loader`` (C3): file → Document
    - ``chunker`` (C4): Document → list[Chunk]
    - ``transforms``: list of ``BaseTransform`` applied in order
    - ``batch_processor`` (C10): Chunk → list[ChunkRecord]
      (carries both dense + sparse vectors)
    - ``vector_upserter`` (C12): ChunkRecord → BaseVectorStore
    - ``bm25_indexer`` (C11): ChunkRecord → inverted index on disk
    - ``file_integrity`` (C2): SHA256 + ingestion history (skip logic)
    - ``image_storage`` (C13, optional): image_id → file path index
    - ``bm25_index_name``: name used by BM25Indexer.save/load
    """

    name = "ingestion_pipeline"

    def __init__(
        self,
        *,
        loader: BaseLoader,
        chunker: "DocumentChunker",
        transforms: list["BaseTransform"],
        batch_processor: "BatchProcessor",
        vector_upserter: "VectorUpserter",
        bm25_indexer: "BM25Indexer",
        file_integrity: FileIntegrityChecker,
        image_storage: "ImageStorage | None" = None,
        bm25_index_name: str = "corpus",
        collection: str = "default",
    ) -> None:
        self.loader = loader
        self.chunker = chunker
        self.transforms = transforms
        self.batch_processor = batch_processor
        self.vector_upserter = vector_upserter
        self.bm25_indexer = bm25_indexer
        self.file_integrity = file_integrity
        self.image_storage = image_storage
        self.bm25_index_name = bm25_index_name
        self.collection = collection

    @staticmethod
    def _image_bytes(data: Any, path: Any) -> bytes | None:
        """Resolve raw image bytes from an inline payload or an on-disk file."""
        if data is not None:
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            if isinstance(data, str):  # base64 inline payload
                return base64.b64decode(data)
            return None
        if path:
            p = Path(path)
            if p.is_file():
                return p.read_bytes()
        return None

    @staticmethod
    def _image_ext(data: Any, path: Any, mime_type: Any) -> str:
        """Pick a storage extension for an image from mime / inline / path."""
        if path:
            suffix = Path(path).suffix.lstrip(".")
            if suffix:
                return suffix
        if mime_type:
            suffix = mimetypes.guess_extension(mime_type) or ""
            if suffix:
                return suffix.lstrip(".").lower()
        if data is not None and not isinstance(data, str):
            # no readable MIME/path — default to png (the vision probe default)
            return "png"
        return "png"

    def _register_images(self, document: Document, run_collection: str) -> int:
        """Stage 2.5: persist + index this document's images (return count).

        The docreader path delivers image BYTES (not pre-written files); the
        adapter keeps those bytes on ``img["data"]`` / ``ImageRef.data``. We
        persist any reachable bytes to ImageStorage, rewrite the image's
        ``path`` to the real file, and clear the inline bytes. The legacy
        PdfLoader path (real on-disk ``path``, no inline data) keeps working
        unchanged.
        """
        if self.image_storage is None or "images" not in document.metadata:
            return 0
        n_images = 0
        saved_paths: dict[str, str] = {}
        for img in document.metadata["images"]:
            img_id = img.get("id")
            if not img_id:
                continue
            try:
                bytes_ = self._image_bytes(img.get("data"), img.get("path"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Cannot read bytes for image %s: %s", img_id, exc)
                continue
            if bytes_ is None:
                continue
            try:
                record = self.image_storage.save(
                    image_id=img_id,
                    image_bytes=bytes_,
                    ext=self._image_ext(
                        img.get("data"), img.get("path"), img.get("mime_type"),
                    ),
                    collection=run_collection,
                    doc_hash=document.metadata.get("doc_hash"),
                    page_num=img.get("page"),
                )
                img["path"] = record.file_path
                img.pop("data", None)
                saved_paths[img_id] = record.file_path
                n_images += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to register image %s: %s", img_id, exc)
        # Patch the structural ``document.images`` ImageRefs so chunk image
        # distribution (which reads ``document.images``) resolves the real
        # persisted path, and clear inline bytes so chunk metadata never
        # serializes raw image payloads.
        for ref in getattr(document, "images", ()) or ():
            if isinstance(ref, dict):
                real = saved_paths.get(ref.get("id"))
                if real:
                    ref["path"] = real
                ref.pop("data", None)
            else:
                real = saved_paths.get(getattr(ref, "id", None))
                if real:
                    ref.path = real
                ref.data = None
        return n_images

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(
        self,
        path: str,
        trace: "TraceContext | None" = None,
        on_progress: "Callable[[str, int, int], None] | None" = None,
        collection: str | None = None,
        source_path: str | None = None,
    ) -> PipelineResult:
        """
        Ingest a single file end-to-end.

        Returns a :class:`PipelineResult` summarizing what was done.
        If the file's SHA256 is in the integrity DB with
        ``status='success'``, the run is a no-op and
        ``PipelineResult.skipped`` is True.

        ``source_path`` (M5): the canonical document identity, which may
        differ from ``path`` — the Web API uploads each file to a unique
        temp path so concurrent same-name uploads never overwrite each
        other, while the document ID / integrity row / chunk metadata must
        keep the stable ``upload_dir/<collection>/<filename>`` label. When
        ``None`` it defaults to ``path`` (CLI / MCP behavior unchanged).

        Tracing (F4): when ``trace`` is provided, each orchestrator
        stage records a single ``record_stage(name, method=...,
        elapsed_ms=..., **extras)`` event. ``trace_type`` should
        be ``"ingestion"`` — the caller (``ingest.py`` / dashboard)
        is responsible for creating it with the right type.

        Progress (F5): when ``on_progress`` is provided, it is
        invoked as ``on_progress(stage_name, current, total)``
        with the following contract:

        - ``load``:      ``("load", 1, 1)``                       on completion
        - ``split``:     ``("split", 1, 1)``                      on completion
        - ``transform``: ``("transform:<name>", 1, 1)``          per transform
        - ``embed``:     ``("embed", batch_idx+1, n_batches)``    per batch
        - ``upsert``:    ``("upsert", 1, 1)``                     on completion

        ``on_progress`` is never called on stage failure (mirrors
        ``_timed`` — only successful work is reported). A
        ``None`` callback disables progress reporting entirely
        without affecting the encode pipeline.
        """
        result = PipelineResult()
        file_path = Path(path)
        # M5: canonical identity may differ from the physical file read.
        canonical_source = source_path or str(file_path)
        result.source_path = canonical_source
        # M3: per-collection scoping. ``run(collection=...)`` wins
        # over the ctor value, mirroring how the Web API injects the
        # routing decision per request.
        run_collection = collection or self.collection

        # ---- Stage 1: integrity check ------------------------------
        try:
            file_hash = self.file_integrity.compute_sha256(str(file_path))
        except Exception as exc:
            if trace is not None:
                trace.record_stage(
                    "integrity", event="error", error=str(exc),
                )
            raise PipelineStageError("integrity", str(exc)) from exc
        result.file_hash = file_hash

        if self.file_integrity.should_skip(file_hash, collection=run_collection):
            logger.info(
                "Skipping %s — already ingested (hash=%s)",
                file_path.name, file_hash[:12],
            )
            result.skipped = True
            if trace is not None:
                trace.record_stage(
                    "ingestion_pipeline", event="skipped",
                    file_hash=file_hash[:12],
                )
            return result

        # ---- Stage 2: load -----------------------------------------
        try:
            document: Document = self._timed(
                trace, "load",
                method=type(self.loader).__name__,
                source=canonical_source,
            )(lambda: self.loader.load(str(file_path)))()
        except Exception as exc:
            if trace is not None:
                trace.record_stage(
                    "load", event="error",
                    method=type(self.loader).__name__,
                    error=str(exc),
                )
            raise PipelineStageError("load", str(exc)) from exc
        if on_progress is not None:
            on_progress("load", 1, 1)
        # M5: the loader labels the document by the physical file it read
        # (a unique temp path for uploads); the chunk metadata / citations
        # must carry the stable canonical source instead.
        document.metadata["source_path"] = canonical_source

        n_images = self._register_images(document, run_collection)
        result.n_images_saved = n_images

        # ---- Stage 3: split (chunk) -------------------------------
        try:
            chunks: list[Chunk] = self._timed(
                trace, "split",
                method=type(self.chunker).__name__,
            )(lambda: self.chunker.split_document(document))()
        except Exception as exc:
            if trace is not None:
                trace.record_stage(
                    "split", event="error",
                    method=type(self.chunker).__name__,
                    error=str(exc),
                )
            raise PipelineStageError("chunk", str(exc)) from exc
        if on_progress is not None:
            on_progress("split", 1, 1)
        result.n_chunks = len(chunks)

        # ---- Stage 4: transforms -----------------------------------
        # Transforms handle their own per-chunk isolation (C5/C6),
        # so a transform-level bug on one chunk doesn't kill the
        # whole batch. A truly fatal bug (e.g. wrong signature) is
        # still re-raised. We record one orchestrator-level event
        # per transform, carrying the transform's own ``name`` as
        # the ``method`` so the Dashboard can break down per-stage
        # latency by transform type.
        for transform in self.transforms:
            try:
                chunks = self._timed(
                    trace, "transform",
                    method=transform.name,
                    n_in=len(chunks),
                )(lambda: transform.transform(chunks, trace=trace))()
            except Exception as exc:
                if trace is not None:
                    trace.record_stage(
                        "transform", event="error",
                        method=transform.name,
                        error=str(exc),
                    )
                raise PipelineStageError(
                    f"transform:{transform.name}", str(exc)
                ) from exc
            if on_progress is not None:
                on_progress(f"transform:{transform.name}", 1, 1)

        if not chunks:
            logger.warning(
                "No chunks produced for %s — stopping here",
                file_path.name,
            )
            self.file_integrity.mark_success(
                file_hash, canonical_source,
                file_size=file_path.stat().st_size,
                collection=run_collection,
            )
            if trace is not None:
                trace.record_stage(
                    "ingestion_pipeline", event="empty_chunks",
                    n_chunks=0,
                )
            return result

        # ---- Stage 5: embed (dense + sparse) ----------------------
        # ``on_progress`` is forwarded to BatchProcessor so the
        # embed stage reports per-batch (the only stage where the
        # total > 1 is meaningful). The progress callback is
        # called inside BatchProcessor.process(); pipeline-level
        # stages call it inline.
        try:
            records: list[ChunkRecord] = self._timed(
                trace, "embed",
                method=type(self.batch_processor).__name__,
                n_in=len(chunks),
            )(lambda: self.batch_processor.process(
                chunks, trace=trace, on_progress=on_progress,
            ))()
        except Exception as exc:
            if trace is not None:
                trace.record_stage(
                    "embed", event="error",
                    method=type(self.batch_processor).__name__,
                    error=str(exc),
                )
            raise PipelineStageError("encode", str(exc)) from exc

        # ---- Stage 6: upsert (vector store + bm25) ---------------
        # Wrap both storage legs under one orchestrator-level event
        # so the Dashboard shows a single "upsert" column. The
        # underlying components already emit their own start/finish
        # events with finer granularity.
        try:
            n_upserted, index = self._timed(
                trace, "upsert",
                method="vector+bm25",
                n_in=len(records),
            )(lambda: self._do_upsert(records, trace=trace))()
        except PipelineStageError as exc:
            # Propagate the inner stage label (vector_store /
            # bm25_store) so existing error-contract tests still
            # see the original ``stage`` attribute. We only add the
            # orchestrator-level trace event here.
            if trace is not None:
                trace.record_stage(
                    "upsert", event="error",
                    method="vector+bm25",
                    stage=exc.stage,
                    error=str(exc),
                )
            raise
        except Exception as exc:
            if trace is not None:
                trace.record_stage(
                    "upsert", event="error",
                    method="vector+bm25",
                    error=str(exc),
                )
            raise PipelineStageError("storage", str(exc)) from exc
        if on_progress is not None:
            on_progress("upsert", 1, 1)
        result.n_records_upserted = n_upserted
        result.bm25_n_docs = index.n_docs

        # ---- Stage 7: mark success ---------------------------------
        try:
            # M5: the integrity row is keyed by the canonical source, not
            # the (possibly unique temp) file read — document identity and
            # re-upload dedup stay stable.
            self.file_integrity.mark_success(
                file_hash, canonical_source,
                file_size=file_path.stat().st_size,
                collection=run_collection,
            )
        except Exception as exc:
            # Storage layer already has the records; we just lost
            # the ability to skip the file next time. Log but don't
            # crash the whole run.
            logger.warning(
                "Failed to mark success in integrity DB: %s", exc,
            )
            result.errors.append(f"integrity_mark_success: {exc}")

        logger.info(
            "Ingested %s: %d chunks → vector store, BM25, %d images",
            file_path.name, result.n_chunks, result.n_images_saved,
        )
        return result

    # ------------------------------------------------------------------
    # Per-stage timing helper (F4)
    # ------------------------------------------------------------------
    def _timed(
        self,
        trace: "TraceContext | None",
        name: str,
        method: str,
        **extra: Any,
    ):
        """
        Return a decorator that records the wall-clock time of the
        wrapped callable on ``trace`` as a single
        ``record_stage(name, method=..., elapsed_ms=..., **extra)``
        event.

        Mirrors :meth:`HybridSearch._timed` so the trace shape is
        uniform across query and ingestion pipelines. If ``trace``
        is None, the decorator is a no-op (the work still runs).
        """
        def decorator(fn):
            def wrapper():
                if trace is None:
                    return fn()
                t0 = time.perf_counter()
                result = fn()
                trace.record_stage(
                    name,
                    method=method,
                    elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                    **extra,
                )
                return result
            return wrapper
        return decorator

    # ------------------------------------------------------------------
    # Combined upsert (F4)
    # ------------------------------------------------------------------
    def _do_upsert(
        self,
        records: list[ChunkRecord],
        *,
        trace: "TraceContext | None",
    ) -> tuple[int, Any]:
        """
        Run the vector-store upsert and BM25 build/save back-to-back,
        mapping each sub-stage failure to a clear PipelineStageError.

        Kept as a single helper so the ``upsert`` orchestrator event
        spans BOTH storage legs in one wall-clock measurement.
        """
        try:
            n_upserted = self.vector_upserter.upsert(records, trace=trace)
        except Exception as exc:
            raise PipelineStageError(
                "vector_store", str(exc),
            ) from exc

        try:
            index = self._merge_into_bm25(records, trace=trace)
        except Exception as exc:
            raise PipelineStageError(
                "bm25_store", str(exc),
            ) from exc

        return n_upserted, index

    def _merge_into_bm25(
        self,
        records: list[ChunkRecord],
        *,
        trace: "TraceContext | None",
    ) -> Any:
        """Merge ``records`` into the on-disk BM25 index (data-consistency).

        First ingest builds the index from scratch; every later ingest
        **loads the existing index and calls ``add()``** so previously
        ingested documents stay sparse-retrievable. (Before this fix the
        pipeline rebuilt the whole index per document, silently dropping
        every other document from BM25.) The merged index is then saved
        atomically.

        The whole read-modify-write runs under the **per-collection write
        lock** so concurrent uploads to the same collection never lose an
        update (last-writer-wins overwrite).
        """
        with bm25_write_lock(self.bm25_index_name):
            try:
                index = self.bm25_indexer.load(
                    self.bm25_index_name, trace=trace,
                )
            except FileNotFoundError:
                index = self.bm25_indexer.build(records, trace=trace)
            else:
                index = self.bm25_indexer.add(index, records, trace=trace)
            self.bm25_indexer.save(
                index, self.bm25_index_name, trace=trace,
            )
        return index
