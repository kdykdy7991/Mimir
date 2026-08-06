"""
Reciprocal Rank Fusion (D4) — combine multiple ranked lists.

The D5 :class:`HybridSearch` runs the dense retriever (D2) and the
sparse retriever (D3) in parallel and gets back two ranked lists
of :class:`RetrievalResult`. Folding them into a single list is
RRF's job::

    RRF_score(d) = Σ_r  1 / (k + rank_r(d))

where ``rank_r(d)`` is the 1-based rank of document ``d`` in
ranking ``r``. Documents that appear in multiple lists get their
per-list contributions summed.

The constant ``k`` smooths the score — larger ``k`` flattens the
ranking (top results get less boost), smaller ``k`` amplifies
differences. Cormack et al. (2009) found ``k=60`` to work well
across collections; that's the default here.

Outputs:
- One :class:`RetrievalResult` per unique chunk_id across all
  input rankings.
- ``score`` is the fused RRF score (NOT comparable to either
  input's raw score — keep in mind when displaying).
- ``source = "fusion"`` to distinguish from dense / sparse /
  rerank results.
- ``rank`` is the new 1-based position in the fused list.
- ``chunk`` (and therefore text + metadata) is taken from the
  FIRST ranking in which the chunk appeared — call order matters
  only for the "first seen wins" tie-breaker, not for the score.

Determinism
-----------
The function is fully deterministic: same inputs → same outputs.
Ties (equal fused scores) are broken by ``chunk_id`` ascending
so the output is stable across runs and processes.
"""

from __future__ import annotations

from typing import Iterable

from src.core.types import RetrievalResult


# Standard default from the original RRF paper. 60 is a safe
# baseline; tuners can adjust.
DEFAULT_K = 60


def reciprocal_rank_fusion(
    rankings: Iterable[Iterable[RetrievalResult]],
    k: int = DEFAULT_K,
) -> list[RetrievalResult]:
    """
    Fuse multiple ranked lists using RRF.

    Args:
        rankings: A sequence of ranked lists. Each list should
            be ordered best-first (rank 1 is the top hit). Empty
            lists are skipped, not treated as errors.
        k: The RRF smoothing constant. Must be >= 1 (k=0 would
            cause division-by-zero on rank 1). Default 60.

    Returns:
        A single fused list of :class:`RetrievalResult`, ordered
        by descending RRF score (with ``chunk_id`` as the
        deterministic tie-breaker). Empty input → empty output.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")

    scores: dict[str, float] = {}
    by_id: dict[str, RetrievalResult] = {}

    for ranking in rankings:
        for rank, result in enumerate(ranking, start=1):
            cid = result.chunk_id
            # First-seen wins for chunk record. Subsequent
            # occurrences contribute to the score but don't
            # overwrite the canonical chunk.
            if cid not in by_id:
                by_id[cid] = result
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)

    if not scores:
        return []

    # Sort: RRF score desc, then chunk_id asc (deterministic).
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))

    fused: list[RetrievalResult] = []
    for new_rank, (cid, fused_score) in enumerate(ordered, start=1):
        original = by_id[cid]
        fused.append(
            RetrievalResult(
                chunk=original.chunk,
                score=float(fused_score),
                rank=new_rank,
                source="fusion",
            )
        )
    return fused


__all__ = ["DEFAULT_K", "reciprocal_rank_fusion"]
