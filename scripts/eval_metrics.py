"""
Pure retrieval-evaluation metrics for the offline Golden Set runner.

Kept free of any retrieval/storage code so the math is unit-testable in
isolation. Ranking lists passed in here are already *tie-normalized*:
see :func:`tie_normalized_rows` — production RRF/BM25 already break ties
by chunk id, but the dense single path does not, so the evaluator
applies ``(score desc, chunk_id asc)`` deterministically before any
rank metric is computed.

Relevance is binary (a chunk is relevant or not). nDCG uses graded
binary gains (rel ∈ {0,1}); ideal ranking puts every relevant chunk
first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


# ---------------------------------------------------------------------------
# Stable ordering
# ---------------------------------------------------------------------------

def tie_normalized_rows(rows: Sequence[dict]) -> list[dict]:
    """Return rows ordered by ``(-score, chunk_id)``.

    Each row needs ``chunk_id`` and ``score`` (float, None allowed →
    treated as -inf). This normalizes equal-score dense results by
    stable chunk id WITHOUT changing production retrieval behavior.
    """

    def key(row: dict) -> tuple:
        score = row.get("score")
        # Higher score first; None scores sort last. Tie → chunk id asc.
        return (-float(score or 0.0), str(row["chunk_id"]))

    return sorted(rows, key=key)


# ---------------------------------------------------------------------------
# Per-list metrics
# ---------------------------------------------------------------------------

def relevant_ranks(retrieved_ids: Sequence[str], relevant: Sequence[str]) -> list[int]:
    """1-based ranks at which any relevant chunk occurs (in order)."""
    wanted = set(relevant)
    return [i for i, cid in enumerate(retrieved_ids, start=1) if cid in wanted]


def recall_at_k(
    retrieved_ids: Sequence[str], relevant: Sequence[str], k: int,
) -> float:
    if not relevant:
        return 0.0
    hits = len(set(retrieved_ids[:k]) & set(relevant))
    return hits / len(set(relevant))


def precision_at_k(
    retrieved_ids: Sequence[str], relevant: Sequence[str], k: int,
) -> float:
    window = retrieved_ids[:k]
    if not window:
        return 0.0
    return len(set(window) & set(relevant)) / len(window)


def reciprocal_rank(
    retrieved_ids: Sequence[str], relevant: Sequence[str],
) -> float:
    ranks = relevant_ranks(retrieved_ids, relevant)
    return 1.0 / ranks[0] if ranks else 0.0


def dcg_at_k(
    retrieved_ids: Sequence[str], relevant: Sequence[str], k: int,
) -> float:
    import math

    wanted = set(relevant)
    gain = 0.0
    for i, cid in enumerate(retrieved_ids[:k], start=1):
        if cid in wanted:
            gain += 1.0 / math.log2(i + 1)
    return gain


def ndcg_at_k(
    retrieved_ids: Sequence[str], relevant: Sequence[str], k: int,
) -> float:
    ideal_hits = min(len(set(relevant)), k)
    if ideal_hits == 0:
        return 0.0
    import math

    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    actual = dcg_at_k(retrieved_ids, relevant, k)
    return actual / ideal if ideal else 0.0


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (p ∈ [0,100]); 0.0 when there are no values."""
    if not values:
        return 0.0
    if not 0 <= p <= 100:
        raise ValueError("p must be in [0, 100]")
    ordered = sorted(values)
    rank = max(1, int(round(p / 100.0 * len(ordered))))
    return ordered[rank - 1]


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass
class Aggregate:
    """Macro/micro aggregates over evaluated cases."""

    answerable_evaluated: int = 0
    recall: dict[str, float] = None  # type: ignore[assignment]
    precision: dict[str, float] = None  # type: ignore[assignment]
    mrr: float = 0.0
    ndcg: dict[str, float] = None  # type: ignore[assignment]
    document_hit_rate: float = 0.0
    chunk_hit_rate: float = 0.0
    no_answer_total: int = 0
    no_answer_correct: int = 0
    no_answer_false_positives: int = 0

    def as_dict(self) -> dict:
        return {
            "answerable_evaluated": self.answerable_evaluated,
            "recall": self.recall,
            "precision": self.precision,
            "mrr": self.mrr,
            "ndcg": self.ndcg,
            "document_hit_rate": self.document_hit_rate,
            "chunk_hit_rate": self.chunk_hit_rate,
            "no_answer_total": self.no_answer_total,
            "no_answer_correct": self.no_answer_correct,
            "no_answer_accuracy": (
                self.no_answer_correct / self.no_answer_total
                if self.no_answer_total else None
            ),
            "no_answer_false_positives": self.no_answer_false_positives,
        }


def aggregate(case_reports: Sequence[dict], ks: Sequence[int]) -> Aggregate:
    """Aggregate per-case reports produced by the eval runner.

    Cases with ``status == "error"`` are infrastructure failures and are
    NEVER folded into recall/precision/no-answer metrics. No-answer cases
    only contribute to the no-answer counters.
    """
    ks = sorted(set(ks))
    answerable = [
        c for c in case_reports
        if c["status"] == "ok" and not c.get("expect_no_answer")
    ]
    no_answer = [
        c for c in case_reports
        if c["status"] == "ok" and c.get("expect_no_answer")
    ]

    agg = Aggregate(answerable_evaluated=len(answerable))
    if answerable:
        agg.recall = {
            f"recall_at_{k}": mean([c["metrics"][f"recall_at_{k}"] for c in answerable])
            for k in ks
        }
        agg.precision = {
            f"precision_at_{k}": mean(
                [c["metrics"][f"precision_at_{k}"] for c in answerable]
            )
            for k in ks
        }
        agg.mrr = mean([c["metrics"]["reciprocal_rank"] for c in answerable])
        agg.ndcg = {
            f"ndcg_at_{k}": mean([c["metrics"][f"ndcg_at_{k}"] for c in answerable])
            for k in ks
        }
        doc_hits = sum(1 for c in answerable if c["metrics"]["document_hit"])
        agg.document_hit_rate = doc_hits / len(answerable)
        total_relevant = sum(len(c["expected_chunk_ids"]) for c in answerable)
        total_retrieved_relevant = sum(
            c["metrics"]["relevant_chunks_retrieved"] for c in answerable
        )
        agg.chunk_hit_rate = (
            total_retrieved_relevant / total_relevant if total_relevant else 0.0
        )
    else:
        agg.recall = {f"recall_at_{k}": None for k in ks}
        agg.precision = {f"precision_at_{k}": None for k in ks}
        agg.ndcg = {f"ndcg_at_{k}": None for k in ks}

    agg.no_answer_total = len(no_answer)
    agg.no_answer_correct = sum(
        1 for c in no_answer if c["metrics"]["returned_count"] == 0
    )
    agg.no_answer_false_positives = agg.no_answer_total - agg.no_answer_correct
    return agg
