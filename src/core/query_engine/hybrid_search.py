"""
HybridSearch (D5) — orchestrator for the D1+D2+D3+D4 chain.

Flow::

    query string
        │
        ▼
    QueryProcessor.process(query)        ← D1: keywords + filters
        │
        ├──► DenseRetriever.retrieve(query, …)    ← D2 (vector path)
        │
        └──► SparseRetriever.retrieve(keywords, …) ← D3 (BM25 path)
        │
        ▼
    Fusion.fuse([dense, sparse])         ← D4: RRF
        │
        ▼
    metadata post-filter (catches sparse-path hits that the
    vector-store filter didn't see)
        │
        ▼
    Top-K slice
        │
        ▼
    list[RetrievalResult]

Tracing (F3)
-----------
When a ``TraceContext`` is passed, every stage above is recorded
as a separate ``record_stage`` event carrying ``method`` and
``elapsed_ms`` (see ``search()`` for the full list). This lets the
Dashboard surface per-stage latency without needing a separate
instrumented build.

Failure handling
----------------
Each path is wrapped in try/except. If dense fails, sparse still
runs and we fuse just the sparse results. If both fail, the
search returns an empty list. This matches the spec's
"任一路径失败时能降级到单路结果" contract.

The metadata post-filter is the spec's "后置过滤兜底": even
when the caller passes ``filters=...`` to the dense retriever
(which the store will use to narrow its query), we also
post-filter the fused results. This catches sparse-path hits
that the BM25 index knows nothing about, and acts as a
defense-in-depth for stores that ignore certain filter shapes.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from src.core.query_engine.dense_retriever import DenseRetriever
from src.core.query_engine.fusion import (
    DEFAULT_K,
    reciprocal_rank_fusion,
)
from src.core.query_engine.query_processor import QueryProcessor
from src.core.query_engine.sparse_retriever import SparseRetriever
from src.core.types import RetrievalResult

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext


logger = logging.getLogger(__name__)


class HybridSearch:
    """
    Wire QueryProcessor + Dense + Sparse + Fusion into a single
    ``search()`` entry point.

    Args:
        query_processor: The D1 component (query → keywords + filters).
        dense_retriever: The D2 component (vector path).
        sparse_retriever: The D3 component (BM25 path).
        fusion: Callable ``(rankings, k) -> list[RetrievalResult]``.
            Defaults to :func:`reciprocal_rank_fusion`. Inject a
            custom fusion in tests to assert behavior.
        rrf_k: The RRF smoothing constant (passed to ``fusion``).
            Default 60.
        retrieval_multiplier: Each retriever is asked for
            ``top_k * retrieval_multiplier`` results before fusion
            — gives the fusion step enough candidates to draw
            from. Default 2.
    """

    name = "hybrid_search"

    def __init__(
        self,
        query_processor: QueryProcessor,
        dense_retriever: DenseRetriever,
        sparse_retriever: SparseRetriever,
        fusion: Callable | None = None,
        *,
        rrf_k: int = DEFAULT_K,
        retrieval_multiplier: int = 2,
    ) -> None:
        self.query_processor = query_processor
        self.dense = dense_retriever
        self.sparse = sparse_retriever
        self.fusion = fusion or reciprocal_rank_fusion
        self.rrf_k = rrf_k
        self.retrieval_multiplier = retrieval_multiplier

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        trace: "TraceContext | None" = None,
    ) -> list[RetrievalResult]:
        """
        Run the full hybrid pipeline and return the top-k chunks.

        When ``trace`` is provided, every stage is recorded
        separately with its ``method`` and ``elapsed_ms`` so a
        downstream reader can see the per-stage breakdown of a
        query run. The stages are:

        - ``query_processing`` — keyword extraction / filter detect
        - ``dense_retrieval``  — vector-store query
        - ``sparse_retrieval`` — BM25 query
        - ``fusion``           — RRF (or pass-through if only one path)
        - ``post_filter``      — metadata exact-match (defence in depth)
        - ``trim``             — top_k slice

        Empty input or top_k <= 0 returns ``[]`` without recording
        any stage (the caller is responsible for short-circuit
        traces at a higher level if they want one).
        """
        if not query or not query.strip():
            return []
        if top_k <= 0:
            return []

        # Step 1: query processing (keywords + auto-filters).
        processed = self._timed(
            trace, "query_processing", "rule",
        )(lambda: self.query_processor.process(query))()
        effective_filters: dict[str, Any] = {
            **processed.filters, **(filters or {}),
        }
        fetch_k = max(1, top_k * self.retrieval_multiplier)

        # Step 2: dense + sparse, in serial execution. The dense
        # and sparse retrievers internally record their own trace
        # events; we record the wrapping here at the orchestrator
        # level so the timing reflects the full call (including
        # network I/O for remote vector stores).
        dense_results = self._timed(
            trace, "dense_retrieval",
            method=type(self.dense).__name__,
            fetch_k=fetch_k,
        )(lambda: self._run_dense(
            query, fetch_k, effective_filters, trace,
        ))()

        sparse_results = self._timed(
            trace, "sparse_retrieval",
            method=type(self.sparse).__name__,
            fetch_k=fetch_k,
        )(lambda: self._run_sparse(
            processed.keywords, fetch_k, trace,
        ))()

        # Step 3: fusion.
        if dense_results and sparse_results:
            fused = self._timed(
                trace, "fusion", f"rrf_k={self.rrf_k}",
                n_dense=len(dense_results),
                n_sparse=len(sparse_results),
            )(lambda: self.fusion(
                [dense_results, sparse_results], k=self.rrf_k,
            ))()
        elif dense_results:
            fused = dense_results
            if trace is not None:
                trace.record_stage(
                    "fusion", event="passthrough",
                    method="dense_only",
                    elapsed_ms=0.0, n_dense=len(dense_results),
                )
        elif sparse_results:
            fused = sparse_results
            if trace is not None:
                trace.record_stage(
                    "fusion", event="passthrough",
                    method="sparse_only",
                    elapsed_ms=0.0, n_sparse=len(sparse_results),
                )
        else:
            if trace is not None:
                trace.record_stage(
                    "hybrid_search", event="no_results",
                )
            return []

        # Step 4: metadata post-filter (defence in depth).
        if filters:
            fused = self._timed(
                trace, "post_filter", "exact_match",
                n_before=len(fused),
            )(lambda: self._apply_metadata_filters(fused, filters))()

        # Step 5: top_k trim.
        fused = self._timed(
            trace, "trim", "top_k_slice",
            top_k=top_k, n_before=len(fused),
        )(lambda: fused[:top_k])()

        return fused

    # ------------------------------------------------------------------
    # Per-stage timing helper
    # ------------------------------------------------------------------
    def _timed(
        self,
        trace: "TraceContext | None",
        name: str,
        method: str,
        **extra: Any,
    ):
        """
        Return a decorator that measures elapsed wall-clock time
        around the wrapped callable and records a single
        ``record_stage(name, elapsed_ms=..., method=..., **extra)``
        event on ``trace``.

        If ``trace`` is None the decorator is a no-op (the work
        still runs, just nothing is recorded).
        """
        def decorator(fn):
            def wrapper():
                t0 = time.perf_counter()
                result = fn()
                if trace is not None:
                    trace.record_stage(
                        name,
                        method=method,
                        elapsed_ms=(
                            (time.perf_counter() - t0) * 1000.0
                        ),
                        **extra,
                    )
                return result
            return wrapper
        return decorator

    # ------------------------------------------------------------------
    # Per-path wrappers
    # ------------------------------------------------------------------
    def _run_dense(
        self,
        query: str,
        fetch_k: int,
        filters: dict[str, Any] | None,
        trace: "TraceContext | None",
    ) -> list[RetrievalResult]:
        try:
            return self.dense.retrieve(query, fetch_k, filters, trace=trace)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dense retrieval failed: %s", exc)
            if trace is not None:
                trace.record_stage(
                    self.name, event="dense_failed", error=str(exc),
                )
            return []

    def _run_sparse(
        self,
        keywords: list[str],
        fetch_k: int,
        trace: "TraceContext | None",
    ) -> list[RetrievalResult]:
        try:
            return self.sparse.retrieve(keywords, fetch_k, trace=trace)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sparse retrieval failed: %s", exc)
            if trace is not None:
                trace.record_stage(
                    self.name, event="sparse_failed", error=str(exc),
                )
            return []

    # ------------------------------------------------------------------
    # Metadata post-filter
    # ------------------------------------------------------------------
    @staticmethod
    def _apply_metadata_filters(
        candidates: list[RetrievalResult],
        filters: dict[str, Any],
    ) -> list[RetrievalResult]:
        """
        Keep only candidates whose ``chunk.metadata`` has ALL of the
        ``(key, value)`` pairs in ``filters`` (exact match).
        """
        if not filters:
            return candidates
        out: list[RetrievalResult] = []
        for r in candidates:
            meta = r.metadata or {}
            if all(meta.get(k) == v for k, v in filters.items()):
                out.append(r)
        return out


__all__ = ["HybridSearch"]
