"""
RerankerStage (D6) — orchestration stage that wraps a `libs.reranker`
backend and bridges it to the D1–D5 RetrievalResult contract.

Flow::

    list[RetrievalResult]          (from HybridSearch.search)
        │
        ▼
    _to_candidates(...)            RetrievalResult → RerankCandidate
        │
        ▼
    reranker.rerank(query, candidates, top_k=None)
        │
        ├── (success) ──► _to_results(...)         RerankCandidate → RetrievalResult
        │                                              source="rerank", new score
        ▼
    RerankOutput(results, fallback=False)

    (any exception during rerank)
        │
        ▼
    RerankOutput(original_results, fallback=True)   ← preserves fusion order

Failure handling
----------------
The D6 contract requires that a reranker failure must not break the
final returned list. We therefore catch **all** exceptions, log a
warning, and return the *original* candidates untouched but flagged
with ``fallback=True``. The caller (CLI / downstream) decides what
to do with the flag.

Two paths short-circuit before the reranker call:

- Empty input → ``RerankOutput([], fallback=False)`` (nothing to do).
- ``NoneReranker`` backend → pass-through with ``fallback=False``
  (the backend itself is the no-op, so we are not "falling back").

Top-m truncation
----------------
If ``top_m`` is set, only the first ``top_m`` candidates are sent
to the reranker; the remainder keep their original fusion rank and
are appended after the reranked head. This matches the spec's
"精排候选数 top_m" config knob.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.core.types import RetrievalResult
from src.libs.reranker.base_reranker import (
    BaseReranker,
    NoneReranker,
    RerankCandidate,
)

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext


logger = logging.getLogger(__name__)


@dataclass
class RerankOutput:
    """
    Result of a rerank stage call.

    Attributes:
        results: The (possibly reordered) candidates. On ``fallback=True``
            this is the **original** list, untouched.
        fallback: ``True`` iff the reranker raised and we returned the
            fusion output unchanged. Downstream code can use this flag
            for metrics / logging.
    """
    results: list[RetrievalResult]
    fallback: bool = False
    error: str | None = None


class RerankerStage:
    """
    Wrap a :class:`BaseReranker` and adapt between RetrievalResult and
    RerankCandidate.

    Args:
        reranker: The configured backend. ``NoneReranker`` is handled
            as a pass-through (no-op, ``fallback=False``).
        top_m: Optional cap on candidates sent to the reranker. When
            set, only the first ``top_m`` of the input list are scored;
            the rest keep their original order and are appended after.
            Useful when reranker cost is high (Cross-Encoder / LLM).
    """

    name = "reranker"

    def __init__(
        self,
        reranker: BaseReranker,
        *,
        top_m: int | None = None,
    ) -> None:
        if top_m is not None and top_m <= 0:
            raise ValueError(f"top_m must be positive, got {top_m}")
        self.reranker = reranker
        self.top_m = top_m

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        trace: "TraceContext | None" = None,
    ) -> RerankOutput:
        """
        Rerank ``candidates`` for ``query``.

        On any reranker exception, returns ``RerankOutput(candidates,
        fallback=True)`` — the original list is preserved as-is.

        Tracing (F3)
        ------------
        When a ``TraceContext`` is provided the rerank stage is
        recorded with a single ``record_stage(self.name, ...)``
        event carrying ``method`` (the backend class name) and
        ``elapsed_ms``. Special events (``empty_input``, ``noop``,
        ``fallback``) carry the same shape so downstream readers
        can rely on a consistent schema.
        """
        t0 = time.perf_counter() if trace is not None else 0.0
        method = type(self.reranker).__name__
        common = {
            "method": method,
            "top_m": self.top_m,
            "n_in": len(candidates),
        }

        # Short-circuit: nothing to do.
        if not candidates:
            if trace is not None:
                trace.record_stage(
                    self.name, event="empty_input",
                    elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                    **common,
                )
            return RerankOutput(results=[], fallback=False)

        # Short-circuit: backend is the explicit no-op. Not a fallback.
        if isinstance(self.reranker, NoneReranker):
            if trace is not None:
                trace.record_stage(
                    self.name, event="noop",
                    elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                    **common,
                )
            return RerankOutput(
                results=list(candidates),
                fallback=False,
            )

        # Split head (to rerank) vs tail (to preserve).
        head, tail = self._split_head_tail(candidates)

        # Adapt to RerankCandidate shape.
        try:
            cand_objs = self._to_candidates(head)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker: failed to build candidates: %s", exc)
            if trace is not None:
                trace.record_stage(
                    self.name, event="fallback",
                    error=str(exc),
                    n_in=len(candidates),
                )
            return RerankOutput(
                results=list(candidates),
                fallback=True,
                error=str(exc),
            )

        # Run the backend.
        try:
            reranked = self.reranker.rerank(query, cand_objs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reranker backend '%s' failed: %s — using fusion order",
                type(self.reranker).__name__, exc,
            )
            if trace is not None:
                trace.record_stage(
                    self.name, event="fallback",
                    error=str(exc),
                    n_in=len(candidates),
                )
            return RerankOutput(
                results=list(candidates),
                fallback=True,
                error=str(exc),
            )

        # Map back to RetrievalResult, preserving originals for IDs we
        # still have, and appending the un-reranked tail at the end.
        try:
            results = self._to_results(head, reranked, tail)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker: failed to map results: %s", exc)
            if trace is not None:
                trace.record_stage(
                    self.name, event="fallback",
                    error=str(exc),
                    n_in=len(candidates),
                )
            return RerankOutput(
                results=list(candidates),
                fallback=True,
                error=str(exc),
            )

        if trace is not None:
            trace.record_stage(
                self.name, event="finish",
                n_in=len(candidates),
                n_out=len(results),
                fallback=False,
            )
        return RerankOutput(results=results, fallback=False)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _split_head_tail(
        self,
        candidates: list[RetrievalResult],
    ) -> tuple[list[RetrievalResult], list[RetrievalResult]]:
        if self.top_m is None or self.top_m >= len(candidates):
            return list(candidates), []
        return list(candidates[: self.top_m]), list(candidates[self.top_m:])

    @staticmethod
    def _to_candidates(
        results: list[RetrievalResult],
    ) -> list[RerankCandidate]:
        return [
            RerankCandidate(
                id=r.chunk_id,
                text=r.text,
                score=r.score,
                metadata=dict(r.metadata or {}),
            )
            for r in results
        ]

    @staticmethod
    def _to_results(
        head: list[RetrievalResult],
        reranked: list[RerankCandidate],
        tail: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        """
        Build the output list.

        Order: reranked candidates first (in their reranker order),
        then any tail items the reranker didn't see. Unknown IDs in
        the reranker output are dropped; head items missing from the
        reranker output fall through with their original score.
        """
        by_id: dict[str, RetrievalResult] = {r.chunk_id: r for r in head}

        out: list[RetrievalResult] = []
        seen: set[str] = set()
        for cand in reranked:
            original = by_id.get(cand.id)
            if original is None:
                # Reranker hallucinated an id we didn't pass in — skip.
                continue
            out.append(RetrievalResult(
                chunk=original.chunk,
                score=float(cand.score),
                rank=len(out),
                source="rerank",
            ))
            seen.add(cand.id)

        # Anything in `head` the reranker dropped — keep with original score.
        for r in head:
            if r.chunk_id in seen:
                continue
            out.append(RetrievalResult(
                chunk=r.chunk,
                score=r.score,
                rank=len(out),
                source="rerank",
            ))

        # Tail (not sent to reranker) keeps original order + source.
        for r in tail:
            out.append(RetrievalResult(
                chunk=r.chunk,
                score=r.score,
                rank=len(out),
                source=r.source or "fusion",
            ))
        return out


__all__ = ["RerankOutput", "RerankerStage"]