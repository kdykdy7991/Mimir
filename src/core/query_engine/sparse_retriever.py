"""
SparseRetriever (D3) — BM25 retrieval over a corpus.

Pipeline:
    keywords (from D1 QueryProcessor)
        → ``bm25_indexer.query(index, keywords, top_k)`` → scored ids
        → ``vector_store.get_by_ids(chunk_ids)`` → text + metadata
        → list of :class:`RetrievalResult`

The BM25 index only knows about ``(term → postings)`` — it has
``chunk_id`` + ``tf`` + ``doc_length`` per posting, but not the
actual chunk text. To produce a useful :class:`RetrievalResult`
(complete with text and metadata for the dashboard / MCP
clients), we use the vector store as a side-channel for the
``id → {text, metadata}`` lookup.

If a chunk id from BM25 is missing from the vector store (e.g.
it was added to BM25 but never upserted to the store), we skip
it rather than fabricate a partial result.

The retriever loads the BM25 index lazily on first use, then
caches it in memory. For long-running processes this avoids
re-reading the JSON from disk on every query.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Sequence

from src.core.types import ChunkRecord, RetrievalResult
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer
from src.libs.vector_store.base_vector_store import BaseVectorStore

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext


logger = logging.getLogger(__name__)


class SparseRetriever:
    """
    BM25 retrieval enriched with full chunk text + metadata.

    Args:
        bm25_indexer: A :class:`BM25Indexer` used for both
            loading the index and running queries.
        vector_store: A :class:`BaseVectorStore` (must implement
            ``get_by_ids``). Used to reverse-lookup text + metadata
            for the chunk_ids BM25 returns.
        index_name: Name of the BM25 index to load on first
            ``retrieve`` call.
        sparse_encoder: Optional tokenizer; currently only used
            if ``retrieve`` is called with a raw string instead
            of pre-tokenized keywords.
    """

    name = "sparse_retriever"

    def __init__(
        self,
        bm25_indexer: BM25Indexer,
        vector_store: BaseVectorStore,
        index_name: str = "corpus",
        sparse_encoder: SparseEncoder | None = None,
    ) -> None:
        self.bm25_indexer = bm25_indexer
        self.vector_store = vector_store
        self.index_name = index_name
        self.tokenizer = sparse_encoder or SparseEncoder()
        self._cached_index: BM25Index | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def retrieve(
        self,
        keywords: Sequence[str],
        top_k: int = 10,
        trace: "TraceContext | None" = None,
    ) -> list[RetrievalResult]:
        """
        Return up to ``top_k`` chunks ranked by BM25 score for
        ``keywords``.

        Args:
            keywords: Pre-tokenized query terms (typically from
                :class:`QueryProcessor`). Order doesn't matter; the
                BM25 indexer handles the term-frequency math.
            top_k: Maximum number of results.

        Returns:
            list[RetrievalResult] in BM25 score order, each with
            ``source="sparse"``. Empty list if no hits.
        """
        if not keywords:
            return []
        if top_k <= 0:
            return []

        if trace is not None:
            trace.record_stage(
                self.name, event="start",
                n_keywords=len(keywords), top_k=top_k,
            )

        # Step 1: ensure the BM25 index is loaded.
        try:
            index = self._get_index()
        except FileNotFoundError as exc:
            # No index on disk yet — caller needs to run ingest
            # first. Bubble up clearly.
            if trace is not None:
                trace.record_stage(
                    self.name, event="index_missing",
                    index_name=self.index_name,
                )
            raise

        # Step 2: BM25 query. The indexer accepts a list of tokens
        # directly (skips the tokenize step in the indexer, since
        # we already have keywords from D1).
        try:
            scored = self.bm25_indexer.query(index, list(keywords), top_k=top_k)
        except Exception:  # noqa: BLE001
            if trace is not None:
                trace.record_stage(self.name, event="query_error")
            raise

        if not scored:
            if trace is not None:
                trace.record_stage(
                    self.name, event="finish", n_out=0,
                )
            return []

        # Step 3: reverse-lookup text + metadata from vector store.
        chunk_ids = [s.chunk_id for s in scored]
        try:
            records = self.vector_store.get_by_ids(chunk_ids)
        except Exception:  # noqa: BLE001
            if trace is not None:
                trace.record_stage(self.name, event="lookup_error")
            raise

        # Build a map id → record. Records missing from the
        # lookup are silently dropped (no partial result).
        by_id: dict[str, dict] = {r["id"]: r for r in records}

        results: list[RetrievalResult] = []
        for rank, s in enumerate(scored, start=1):
            rec = by_id.get(s.chunk_id)
            if rec is None:
                continue
            chunk = ChunkRecord(
                id=rec["id"],
                text=rec.get("text") or "",
                metadata=dict(rec.get("metadata") or {}),
                start_offset=0,
                end_offset=0,
            )
            results.append(
                RetrievalResult(
                    chunk=chunk,
                    score=float(s.score),
                    rank=rank,
                    source="sparse",
                )
            )

        if trace is not None:
            trace.record_stage(
                self.name, event="finish",
                n_out=len(results),
            )
        return results

    # ------------------------------------------------------------------
    # Index caching
    # ------------------------------------------------------------------
    def _get_index(self) -> BM25Index:
        if self._cached_index is None:
            self._cached_index = self.bm25_indexer.load(self.index_name)
        return self._cached_index

    def invalidate_cache(self) -> None:
        """Drop the cached index — call after re-ingestion."""
        self._cached_index = None
