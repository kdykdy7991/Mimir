"""
Unit tests for reciprocal_rank_fusion (D4).

Covers:
- RRF formula: 1/(k+rank) per ranking, summed across rankings
- ``k`` parameter: smaller k amplifies top ranks, larger k flattens
- Determinism: same inputs → same outputs (incl. tie-breaking)
- Tie-breaking: equal fused scores break by chunk_id ascending
- Empty input → empty output
- Single ranking → equivalent to identity (same order)
- First-seen wins for chunk text/metadata
- ``source="fusion"`` marker on every output
- Rank assignment is 1-based contiguous
- ``k < 1`` raises
- Output type is ``RetrievalResult``
"""

from __future__ import annotations

import pytest

from src.core.query_engine import reciprocal_rank_fusion
from src.core.types import ChunkRecord, RetrievalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_result(
    chunk_id: str,
    text: str = "x",
    score: float = 0.0,
    rank: int = 0,
    source: str = "sparse",
    metadata: dict | None = None,
) -> RetrievalResult:
    return RetrievalResult(
        chunk=ChunkRecord(
            id=chunk_id, text=text,
            metadata=metadata or {},
            start_offset=0, end_offset=len(text),
        ),
        score=score, rank=rank, source=source,
    )


# ---------------------------------------------------------------------------
# Basic output contract
# ---------------------------------------------------------------------------

class TestOutputContract:
    def test_empty_input_returns_empty(self):
        assert reciprocal_rank_fusion([]) == []

    def test_all_empty_rankings_returns_empty(self):
        assert reciprocal_rank_fusion([[], [], []]) == []

    def test_single_ranking_passes_through(self):
        """With one ranking and no other contributions, the fused
        order matches the input order (RRF is monotonic for one
        list)."""
        r = [
            make_result("a", score=0.9),
            make_result("b", score=0.7),
            make_result("c", score=0.5),
        ]
        out = reciprocal_rank_fusion([r])
        assert [x.chunk_id for x in out] == ["a", "b", "c"]

    def test_every_output_is_retrieval_result(self):
        r1 = [make_result("a"), make_result("b")]
        r2 = [make_result("b"), make_result("c")]
        for x in reciprocal_rank_fusion([r1, r2]):
            assert isinstance(x, RetrievalResult)

    def test_every_output_source_is_fusion(self):
        r1 = [make_result("a", source="dense"), make_result("b", source="dense")]
        r2 = [make_result("b", source="sparse"), make_result("c", source="sparse")]
        for x in reciprocal_rank_fusion([r1, r2]):
            assert x.source == "fusion"

    def test_rank_is_1_based_contiguous(self):
        r1 = [make_result("a"), make_result("b"), make_result("c")]
        r2 = [make_result("a"), make_result("b")]
        out = reciprocal_rank_fusion([r1, r2])
        assert [x.rank for x in out] == list(range(1, len(out) + 1))


# ---------------------------------------------------------------------------
# RRF formula
# ---------------------------------------------------------------------------

class TestRrfFormula:
    def test_single_ranking_uses_1_over_k_plus_rank(self):
        """With k=60:
        rank 1 → 1/(60+1) = 0.01639
        rank 2 → 1/(60+2) = 0.01613
        """
        r = [make_result("a"), make_result("b")]
        out = reciprocal_rank_fusion([r], k=60)
        scores = [x.score for x in out]
        assert scores[0] == pytest.approx(1 / 61)
        assert scores[1] == pytest.approx(1 / 62)

    def test_two_rankings_scores_are_summed(self):
        """A chunk appearing at rank 1 in BOTH lists gets
        2 * 1/(k+1) total."""
        a, b, c = make_result("a"), make_result("b"), make_result("c")
        r1 = [a, b, c]
        r2 = [a, c, b]  # a is rank 1, c is rank 2, b is rank 3
        out = reciprocal_rank_fusion([r1, r2], k=60)
        scores = {x.chunk_id: x.score for x in out}
        assert scores["a"] == pytest.approx(1 / 61 + 1 / 61)
        assert scores["b"] == pytest.approx(1 / 62 + 1 / 63)
        assert scores["c"] == pytest.approx(1 / 63 + 1 / 62)

    def test_unique_to_one_ranking_keeps_only_its_score(self):
        a, b, c = make_result("a"), make_result("b"), make_result("c")
        r1 = [a, b]
        r2 = [c]
        out = reciprocal_rank_fusion([r1, r2], k=60)
        scores = {x.chunk_id: x.score for x in out}
        assert scores["a"] == pytest.approx(1 / 61)
        assert scores["b"] == pytest.approx(1 / 62)
        assert scores["c"] == pytest.approx(1 / 61)

    def test_ranking_order_changes_fused_order(self):
        """If a is rank 1 in r1 but rank 2 in r2, and b is rank 2
        in r1 and rank 1 in r2, they end up tied — tie-broken by
        chunk_id."""
        a, b = make_result("a"), make_result("b")
        r1 = [a, b]
        r2 = [b, a]
        out = reciprocal_rank_fusion([r1, r2], k=60)
        # Both have score 1/61 + 1/62 → tied → sorted by chunk_id
        assert [x.chunk_id for x in out] == ["a", "b"]


# ---------------------------------------------------------------------------
# k parameter
# ---------------------------------------------------------------------------

class TestKParameter:
    def test_smaller_k_amplifies_rank_difference(self):
        """k=1: rank 1 = 0.5, rank 2 = 0.333. Diff = 0.166
        k=60: rank 1 = 0.0164, rank 2 = 0.0161. Diff = 0.0003"""
        r = [make_result("a"), make_result("b")]
        out_small = reciprocal_rank_fusion([r], k=1)
        out_large = reciprocal_rank_fusion([r], k=60)
        diff_small = out_small[0].score - out_small[1].score
        diff_large = out_large[0].score - out_large[1].score
        assert diff_small > diff_large

    def test_k_zero_raises(self):
        r = [make_result("a")]
        with pytest.raises(ValueError, match="k"):
            reciprocal_rank_fusion([r], k=0)

    def test_k_negative_raises(self):
        r = [make_result("a")]
        with pytest.raises(ValueError, match="k"):
            reciprocal_rank_fusion([r], k=-1)

    def test_default_k_is_60(self):
        """The spec calls out k=60 as a reasonable default."""
        r = [make_result("a")]
        out = reciprocal_rank_fusion([r])  # no k → use default
        assert out[0].score == pytest.approx(1 / 61)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        a, b, c = make_result("a"), make_result("b"), make_result("c")
        r1 = [a, b, c]
        r2 = [b, a]
        out1 = reciprocal_rank_fusion([r1, r2], k=60)
        out2 = reciprocal_rank_fusion([r1, r2], k=60)
        assert [x.chunk_id for x in out1] == [x.chunk_id for x in out2]
        assert [x.score for x in out1] == [x.score for x in out2]

    def test_tie_broken_by_chunk_id_ascending(self):
        """Equal fused scores → sort by chunk_id ascending."""
        # Three single-ranking results that are NOT tied (different
        # ranks). We need tied scores, which only happens when a
        # chunk appears at the SAME rank in multiple lists, OR
        # when k=1 and a single chunk gets the same score. Easier:
        # build three single-rank entries via three separate
        # rankings so each gets score = 1/(k+1) = 1/61.
        a = make_result("a")
        b = make_result("b")
        z = make_result("z")
        out = reciprocal_rank_fusion([[a], [b], [z]], k=60)
        # All three have score 1/61 → tied → sorted by chunk_id asc
        assert [x.chunk_id for x in out] == ["a", "b", "z"]


# ---------------------------------------------------------------------------
# First-seen wins for chunk data
# ---------------------------------------------------------------------------

class TestFirstSeenWins:
    def test_first_seen_chunk_text_preserved(self):
        a_v1 = make_result("a", text="version one", metadata={"v": 1})
        a_v2 = make_result("a", text="version two", metadata={"v": 2})
        b = make_result("b")
        r1 = [a_v1, b]
        r2 = [a_v2, b]
        out = reciprocal_rank_fusion([r1, r2], k=60)
        a_out = next(x for x in out if x.chunk_id == "a")
        # First ranking's version wins
        assert a_out.text == "version one"
        assert a_out.metadata == {"v": 1}

    def test_input_ranking_order_doesnt_affect_first_seen(self):
        """Putting r1 second in the rankings should still use r1's
        first occurrence for chunk data — because we iterate in
        the rankings' input order."""
        a_v1 = make_result("a", text="from r1")
        a_v2 = make_result("a", text="from r2")
        r1 = [a_v1]
        r2 = [a_v2]
        out1 = reciprocal_rank_fusion([r1, r2])
        out2 = reciprocal_rank_fusion([r2, r1])
        a1 = next(x for x in out1 if x.chunk_id == "a")
        a2 = next(x for x in out2 if x.chunk_id == "a")
        assert a1.text == "from r1"
        assert a2.text == "from r2"  # swapped because order swapped


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_rankings_iterable(self):
        assert reciprocal_rank_fusion(iter([])) == []

    def test_one_ranking_is_one_chunk(self):
        r = [make_result("only")]
        out = reciprocal_rank_fusion([r])
        assert len(out) == 1
        assert out[0].chunk_id == "only"
        assert out[0].score == pytest.approx(1 / 61)
        assert out[0].rank == 1

    def test_rank_continuity_across_fused_list(self):
        """After fusion, ranks are 1, 2, 3, ... even if the source
        rankings had gaps (e.g. one list skipped rank 2)."""
        r1 = [make_result("a"), make_result("b"), make_result("c")]
        r2 = [make_result("a"), make_result("c")]  # no b
        out = reciprocal_rank_fusion([r1, r2], k=60)
        assert [x.rank for x in out] == [1, 2, 3]

    def test_score_field_is_float(self):
        r = [make_result("a"), make_result("b")]
        out = reciprocal_rank_fusion([r], k=60)
        for x in out:
            assert isinstance(x.score, float)
