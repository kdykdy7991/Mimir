"""
DenseRetriever (D2) — semantic search over a vector store.

Pipeline:
    query string
        → ``embedding.embed_single(query)`` (BaseEmbedding)
        → ``vector_store.query(vector, top_k, filters)`` (BaseVectorStore)
        → list of :class:`QueryResult` from the store
        → list of :class:`RetrievalResult` (Core / D1 type)

The retriever is a thin orchestrator. The heavy lifting lives in
the injected ``embedding`` and ``vector_store`` — both follow the
``BaseEmbedding`` / ``BaseVectorStore`` contracts, so any provider
combination that satisfies the interfaces works (OpenAI +
ChromaDB, sentence-transformers + Qdrant, etc.).

The returned :class:`RetrievalResult` carries a
``source="dense"`` marker; the D4 fusion step uses this to
identify which list a result came from when merging.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.core.types import ChunkRecord, RetrievalResult
from src.libs.embedding.base_embedding import (
    BaseEmbedding,
    EmbeddingError,
)
from src.libs.vector_store.base_vector_store import BaseVectorStore

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext


logger = logging.getLogger(__name__)


class DenseRetriever:
    """
    Embed a query with the configured :class:`BaseEmbedding` and
    search the configured :class:`BaseVectorStore` for the top-k
    most similar chunks.

    Args:
        embedding: Anything with an ``embed_single(text) -> list[float]``
            method. Typically a :class:`BaseEmbedding` implementation.
        vector_store: Anything with a
            ``query(vector, top_k, filters) -> list[QueryResult]``
            method. Typically a :class:`BaseVectorStore` implementation.
    """

    name = "dense_retriever"

    def __init__(
        self,
        embedding: BaseEmbedding,
        vector_store: BaseVectorStore,
    ) -> None:
        self.embedding = embedding
        self.vector_store = vector_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        trace: "TraceContext | None" = None,
    ) -> list[RetrievalResult]:
        """
        Return up to ``top_k`` chunks most similar to ``query``.

        Args:
            query: The user's natural-language query. Empty or
                whitespace-only input returns ``[]`` without
                touching the embedding client.
            top_k: Maximum number of results to return.
            filters: Optional metadata filter dict passed through
                to the vector store.

        Returns:
            list[RetrievalResult] in score-descending order, each
            carrying the full ``ChunkRecord`` plus a
            ``source="dense"`` marker.

        Raises:
            EmbeddingError: If the embedding client fails. (The
                pipeline layers above can decide to fall back to
                sparse-only search.)
            VectorStoreError: If the store's query fails.
        """
        if not query or not query.strip():
            return []
        if top_k <= 0:
            return []

        if trace is not None:
            trace.record_stage(
                self.name, event="start", query=query[:64], top_k=top_k,
            )

        # Step 1: embed the query.
        try:
            vector = self.embedding.embed_single(query)
        except EmbeddingError:
            if trace is not None:
                trace.record_stage(self.name, event="embedding_error")
            raise
        if not vector:
            # Embedding client returned no vector (e.g. empty
            # embedding). Treat as no result rather than a crash.
            if trace is not None:
                trace.record_stage(self.name, event="empty_embedding")
            return []

        # Step 2: query the vector store.
        try:
            hits = self.vector_store.query(vector, top_k, filters)
        except Exception:  # noqa: BLE001
            if trace is not None:
                trace.record_stage(self.name, event="store_error")
            raise

        # Step 3: convert VectorStore hits → Core RetrievalResult.
        results: list[RetrievalResult] = []
        for rank, hit in enumerate(hits, start=1):
            # Build a minimal ChunkRecord from the store's
            # QueryResult (id, text, metadata). We don't have
            # offsets or vector data here — those live in the
            # store's source data and aren't needed for
            # retrieval-time display.
            chunk = ChunkRecord(
                id=hit.id,
                text=hit.text or "",
                metadata=dict(hit.metadata or {}),
                start_offset=0,
                end_offset=0,
            )
            results.append(
                RetrievalResult(
                    chunk=chunk,
                    score=float(hit.score),
                    rank=rank,
                    source="dense",
                )
            )

        if trace is not None:
            trace.record_stage(
                self.name, event="finish",
                n_out=len(results),
            )
        return results
