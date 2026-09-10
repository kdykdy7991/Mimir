"""
``EngineCache`` — per-collection ``HybridSearch`` / ``IngestionPipeline``
registry built once at app boot and shared across requests.

Background
----------
The M3 multi-collection feature needs every knowledge-base
collection to have its own:

- ``HybridSearch`` (so queries route to the right dense + sparse
  indexes + BM25 index)
- ``IngestionPipeline`` (so uploads write to the right vector /
  BM25 / image store / integrity row)

``scripts.query.build_query_components`` and
``scripts.ingest.build_pipeline`` already accept a ``collection=``
parameter; this cache wraps them with a per-name ``dict`` so we
don't rebuild the same engine twice in the same process.

The cache is **not** persistent. New collections created at runtime
become accessible as soon as the router / manager see them on disk
— the next request triggers the lazy build. Call :meth:`prime` at
boot to surface config / wiring errors eagerly.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EngineCache:
    """Lazily builds and caches per-collection engines.

    Args:
        settings: Loaded :class:`src.core.settings.Settings`.
        data_dir: Root of the on-disk layout
            (``<data_dir>/db/...`` + images).
        embedding: Shared embedding model (one instance handles
            every collection).
        vector_store: A :class:`MultiCollectionVectorStore` router
            (shared across collections).
        sparse_encoder: Optional tokenizer; defaults to a fresh
            :class:`SparseEncoder`.
        splitter: Shared document splitter.
        llm: Optional LLM (passed to transforms).
    """

    def __init__(
        self,
        *,
        settings: Any,
        data_dir: str,
        embedding: Any,
        vector_store: Any,
        sparse_encoder: Any | None = None,
        splitter: Any = None,
        llm: Any = None,
    ) -> None:
        self._settings = settings
        self._data_dir = data_dir
        self._embedding = embedding
        self._vector_store = vector_store
        self._sparse_encoder = sparse_encoder
        self._splitter = splitter
        self._llm = llm
        self._lock = threading.Lock()
        self._hybrid: dict[str, Any] = {}
        self._pipeline: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Read accessors (no construction)
    # ------------------------------------------------------------------
    @property
    def vector_store(self) -> Any:
        return self._vector_store

    @property
    def embedding(self) -> Any:
        return self._embedding

    @property
    def settings(self) -> Any:
        return self._settings

    @property
    def data_dir(self) -> str:
        return self._data_dir

    # ------------------------------------------------------------------
    # Per-collection builders
    # ------------------------------------------------------------------
    def hybrid_for(self, collection: str) -> Any:
        """Return (lazily constructing) the ``HybridSearch`` for ``collection``."""
        with self._lock:
            cached = self._hybrid.get(collection)
            if cached is not None:
                return cached
            engine = self._build_hybrid(collection)
            self._hybrid[collection] = engine
            logger.debug("built HybridSearch for collection=%r", collection)
            return engine

    def pipeline_for(self, collection: str) -> Any:
        """Return (lazily constructing) the ``IngestionPipeline`` for ``collection``."""
        with self._lock:
            cached = self._pipeline.get(collection)
            if cached is not None:
                return cached
            pipeline = self._build_pipeline(collection)
            self._pipeline[collection] = pipeline
            logger.debug("built IngestionPipeline for collection=%r", collection)
            return pipeline

    def prime(self, collections: list[str]) -> None:
        """Eagerly build engines for the given collections.

        Useful at app boot so a missing BM25 index or a broken
        Chroma handle raises during startup rather than during the
        first query / upload.
        """
        for name in collections:
            self.hybrid_for(name)
            self.pipeline_for(name)

    def invalidate_collection(self, collection: str) -> None:
        """Drop the cached engines for ``collection``.

        Called after an ingest or delete touches the collection's
        on-disk BM25 index: the next access lazily rebuilds the
        ``HybridSearch`` / ``IngestionPipeline``, and the fresh
        ``SparseRetriever`` reloads the updated index from disk —
        so the sparse path sees the change without a process restart.
        """
        with self._lock:
            self._hybrid.pop(collection, None)
            self._pipeline.pop(collection, None)
        logger.debug("invalidated engines for collection=%r", collection)

    # ------------------------------------------------------------------
    # Internal builders — call the existing scripts/* wiring kit.
    # ------------------------------------------------------------------
    def _build_hybrid(self, collection: str) -> Any:
        from scripts.query import build_query_components

        return build_query_components(
            data_dir=self._data_dir,
            collection=collection,
            embedding=self._embedding,
            # Scope the router to this collection so DenseRetriever /
            # SparseRetriever calls (which don't carry a ``collection``
            # kwarg) hit the right per-collection Chroma store.
            vector_store=self._scoped_store(collection),
            sparse_encoder=self._sparse_encoder,
        )

    def _build_pipeline(self, collection: str) -> Any:
        from scripts.ingest import build_pipeline

        # Phase 7: honor document_parser.backend the same way the CLI does.
        # resolve_document_parser returns None for legacy/disabled (→ legacy
        # LoaderRegistry) and fails fast on a docreader build/transport failure.
        from src.document_parser.factory import resolve_document_parser
        document_parser = resolve_document_parser(self._settings.document_parser)

        return build_pipeline(
            settings=self._settings,
            data_dir=self._data_dir,
            collection=collection,
            splitter=self._splitter,
            embedding=self._embedding,
            # Scope the router so the VectorUpserter's ``upsert`` call
            # (which doesn't carry ``collection``) writes to the right
            # per-collection Chroma store.
            vector_store=self._scoped_store(collection),
            llm=self._llm,
            document_parser=document_parser,
        )

    def _scoped_store(self, collection: str) -> Any:
        """Wrap the shared router in a per-collection adapter.

        ``ScopedCollectionVectorStore`` forwards every call to the
        ``MultiCollectionVectorStore`` with ``collection=`` fixed, so
        the legacy retrievers / upserter (no collection kwarg) route
        correctly without changing their call sites.
        """
        from src.libs.vector_store.scoped import ScopedCollectionVectorStore

        return ScopedCollectionVectorStore(self._vector_store, collection)


__all__ = ["EngineCache"]