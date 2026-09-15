"""
Unit tests for the pure metrics layer of the retrieval evaluator
(Task 01.4). No retrieval/storage code is touched here; ranking lists
are hand-built.
"""

from __future__ import annotations

import math

import pytest

from scripts.eval_metrics import (
    aggregate,
    mean,
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    tie_normalized_rows,
)


def _rows(ids_scores):
    return [
        {"chunk_id": cid, "score": score} for cid, score in ids_scores
    ]


# ---------------------------------------------------------------------------
# Tie normalization
# ---------------------------------------------------------------------------

def test_tie_normalization_orders_score_desc_then_chunk_id_asc():
    rows = _rows([
        ("b", 0.5), ("a", 0.5), ("d", 0.9), ("c", 0.0), ("e", None),
    ])
    ordered = tie_normalized_rows(rows)
    assert [r["chunk_id"] for r in ordered] == ["d", "a", "b", "c", "e"]


def test_tie_normalization_is_deterministic_across_input_permutations():
    rows_a = _rows([("x", 0.2), ("y", 0.2), ("z", 0.2)])
    rows_b = _rows([("z", 0.2), ("x", 0.2), ("y", 0.2)])
    assert tie_normalized_rows(rows_a) == tie_normalized_rows(rows_b)


def test_tie_normalization_does_not_mutate_production_input():
    rows = _rows([("b", 0.1), ("a", 0.9)])
    original = [dict(r) for r in rows]
    tie_normalized_rows(rows)
    assert rows == original


# ---------------------------------------------------------------------------
# Recall / precision / RR / nDCG
# ---------------------------------------------------------------------------

def test_recall_at_k_partial_and_empty_relevant():
    assert recall_at_k(["a", "b", "c"], ["a", "c"], 1) == 0.5
    assert recall_at_k(["a", "b", "c"], ["a", "c"], 3) == 1.0
    assert recall_at_k(["a"], [], 3) == 0.0


def test_precision_at_k_window_beyond_returned_list():
    assert precision_at_k(["a", "x"], ["a", "z"], 5) == 0.5
    assert precision_at_k([], ["a"], 5) == 0.0


def test_reciprocal_rank_first_hit_only():
    assert reciprocal_rank(["x", "y", "a"], ["a"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x"], ["a"]) == 0.0


def test_ndcg_perfect_order_is_one_demotion_lowers_score():
    # With binary relevance, swapping two RELEVANT items is DCG-neutral;
    # demoting the relevant item below an irrelevant one is what hurts.
    relevant = ["a"]
    assert ndcg_at_k(["a", "x"], relevant, 2) == pytest.approx(1.0)
    demoted = ndcg_at_k(["x", "a"], relevant, 2)
    assert demoted == pytest.approx(1 / math.log2(3))
    assert demoted < 1.0
    # Two equally relevant items: either order is ideal.
    assert ndcg_at_k(["a", "b"], ["a", "b"], 2) == pytest.approx(1.0)
    assert ndcg_at_k(["b", "a"], ["a", "b"], 2) == pytest.approx(1.0)


def test_ndcg_miss_is_zero_and_single_relevant_at_one_is_one():
    assert ndcg_at_k(["x", "y"], ["a"], 2) == 0.0
    assert ndcg_at_k(["a", "x"], ["a"], 2) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Percentile / mean
# ---------------------------------------------------------------------------

def test_percentile_nearest_rank():
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 50) == 2.0
    assert percentile(values, 95) == 4.0
    assert percentile([], 50) == 0.0
    with pytest.raises(ValueError):
        percentile(values, 101)


def test_mean_empty_is_zero():
    assert mean([]) == 0.0
    assert mean([2.0, 4.0]) == 3.0


# ---------------------------------------------------------------------------
# Aggregate: the critical semantics for Task 01
# ---------------------------------------------------------------------------

def _ok_case(cid, expected, retrieved, *, no_answer=False):
    relevant = set(expected)
    retrieved_ids = [r[0] for r in retrieved]
    return {
        "id": cid,
        "status": "ok",
        "expect_no_answer": no_answer,
        "expected_chunk_ids": list(expected),
        "metrics": {
            "recall_at_1": recall_at_k(retrieved_ids, expected, 1),
            "recall_at_5": recall_at_k(retrieved_ids, expected, 5),
            "precision_at_1": precision_at_k(retrieved_ids, expected, 1),
            "precision_at_5": precision_at_k(retrieved_ids, expected, 5),
            "reciprocal_rank": reciprocal_rank(retrieved_ids, expected),
            "ndcg_at_1": ndcg_at_k(retrieved_ids, expected, 1),
            "ndcg_at_5": ndcg_at_k(retrieved_ids, expected, 5),
            "document_hit": bool(retrieved_ids),
            "relevant_chunks_retrieved": len(set(retrieved_ids) & relevant),
            "returned_count": len(retrieved_ids),
            "returned_chars": sum(len(r[0]) for r in retrieved),
        },
    }


def test_aggregate_ranks_and_hit_rates():
    cases = [
        _ok_case("c1", ["a"], [("a", 1.0), ("x", 0.5)]),
        _ok_case("c2", ["b"], [("y", 0.9), ("b", 0.1)]),
    ]
    agg = aggregate(cases, [1, 5]).as_dict()
    assert agg["answerable_evaluated"] == 2
    assert agg["recall"]["recall_at_1"] == 0.5
    assert agg["recall"]["recall_at_5"] == 1.0
    assert agg["mrr"] == pytest.approx((1.0 + 0.5) / 2)
    assert agg["document_hit_rate"] == 1.0
    assert agg["chunk_hit_rate"] == 1.0
    assert agg["no_answer_total"] == 0
    assert agg["no_answer_accuracy"] is None


def test_aggregate_no_answer_false_positives_and_correct():
    cases = [
        _ok_case("na1", [], [("x", 0.0)], no_answer=True),   # FP
        _ok_case("na2", [], [], no_answer=True),             # correct
    ]
    agg = aggregate(cases, [1, 5]).as_dict()
    assert agg["answerable_evaluated"] == 0
    assert agg["no_answer_total"] == 2
    assert agg["no_answer_correct"] == 1
    assert agg["no_answer_false_positives"] == 1
    assert agg["no_answer_accuracy"] == 0.5


def test_aggregate_infrastructure_errors_are_never_misses():
    """An upstream failure must not lower recall and must not be counted
    as either a miss or a no-answer result."""
    cases = [
        _ok_case("c1", ["a"], [("a", 1.0)]),
        {
            "id": "broken",
            "status": "error",
            "expect_no_answer": False,
            "error_kind": "RuntimeError",
            "error": "embedding provider down",
        },
        {
            "id": "broken-na",
            "status": "error",
            "expect_no_answer": True,
            "error_kind": "ConnectionError",
            "error": "reset by peer",
        },
    ]
    agg = aggregate(cases, [1, 5]).as_dict()
    assert agg["answerable_evaluated"] == 1
    assert agg["recall"]["recall_at_1"] == 1.0
    assert agg["mrr"] == 1.0
    assert agg["no_answer_total"] == 0
    assert agg["no_answer_correct"] == 0
    assert agg["no_answer_false_positives"] == 0


def test_aggregate_all_errors_yields_none_metrics_not_zeros():
    agg = aggregate([
        {"id": "e", "status": "error", "expect_no_answer": False},
    ], [1, 5]).as_dict()
    assert agg["answerable_evaluated"] == 0
    assert agg["recall"] == {"recall_at_1": None, "recall_at_5": None}
    assert agg["precision"] == {"precision_at_1": None, "precision_at_5": None}
    assert agg["ndcg"] == {"ndcg_at_1": None, "ndcg_at_5": None}
    assert agg["mrr"] == 0.0
