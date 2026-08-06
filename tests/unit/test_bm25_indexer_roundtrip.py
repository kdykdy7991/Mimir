"""
Roundtrip + correctness tests for BM25Indexer (C11).

Covers:
- Build from ChunkRecord list
- IDF accuracy against the spec's formula
- Save → load → re-query yields stable top ids
- Incremental add (replaces re-ingested chunks, updates df/idf)
- Query: top-k ordering, ties broken by chunk_id, zero score filtered
- BM25 scoring: idf * tf_norm accumulation per chunk
- Edge cases: empty input, empty query, single-doc, large corpus
- k1 / b validation
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from src.core.trace.trace_context import new_trace
from src.core.types import ChunkRecord
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage import (
    DEFAULT_PERSIST_DIR,
    BM25Index,
    BM25Indexer,
    Posting,
    ScoredDoc,
    TermEntry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_record(chunk_id: str, text: str) -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        text=text,
        metadata={"source_path": "/tmp/x.pdf"},
        start_offset=0,
        end_offset=len(text),
    )


@pytest.fixture
def indexer(tmp_path) -> BM25Indexer:
    return BM25Indexer(
        persist_dir=str(tmp_path / "bm25"),
        sparse_encoder=SparseEncoder(),
    )


@pytest.fixture
def sample_corpus() -> list[ChunkRecord]:
    """Five-doc mini-corpus, varied vocabulary, easy to hand-verify."""
    return [
        make_record("c1", "the quick brown fox jumps over the lazy dog"),
        make_record("c2", "the lazy dog sleeps all day long"),
        make_record("c3", "the quick brown rabbit hops quickly"),
        make_record("c4", "foxes are clever animals"),
        make_record("c5", "brown bears are not foxes"),
    ]


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

class TestBuild:
    def test_empty_corpus_produces_empty_index(self, indexer):
        idx = indexer.build([])
        assert idx.n_docs == 0
        assert idx.avgdl == 0.0
        assert idx.terms == {}

    def test_n_docs_set(self, indexer, sample_corpus):
        idx = indexer.build(sample_corpus)
        assert idx.n_docs == 5

    def test_avgdl_matches_corpus_average(self, indexer, sample_corpus):
        idx = indexer.build(sample_corpus)
        expected = sum(len(r.text.split()) for r in sample_corpus) / 5
        # Token-level (after stopword filtering) avgdl, which is
        # less than or equal to the whitespace split count.
        assert idx.avgdl > 0
        assert idx.avgdl <= expected

    def test_every_term_has_idf_and_postings(self, indexer, sample_corpus):
        idx = indexer.build(sample_corpus)
        for term, entry in idx.terms.items():
            assert isinstance(entry, TermEntry)
            assert isinstance(entry.idf, float)
            assert entry.postings
            for p in entry.postings:
                assert isinstance(p, Posting)
                assert p.tf >= 1
                assert p.doc_length >= p.tf

    def test_postings_sorted_by_chunk_id(self, indexer, sample_corpus):
        """Stable iteration order matters for query determinism."""
        idx = indexer.build(sample_corpus)
        for entry in idx.terms.values():
            ids = [p.chunk_id for p in entry.postings]
            assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# IDF accuracy (the spec calls this out explicitly)
# ---------------------------------------------------------------------------

class TestIdfAccuracy:
    def test_idf_matches_spec_formula(self, indexer, sample_corpus):
        """IDF = log((N - df + 0.5) / (df + 0.5) + 1) — the ``+1``
        (Lucene / BM25+) form, matching SparseEncoder."""
        idx = indexer.build(sample_corpus)
        n = idx.n_docs
        for term, entry in idx.terms.items():
            df_t = len(entry.postings)
            expected = math.log((n - df_t + 0.5) / (df_t + 0.5) + 1.0)
            assert entry.idf == pytest.approx(expected, rel=1e-9), (
                f"term {term!r}: idf={entry.idf} vs expected {expected}"
            )

    def test_idf_matches_sparse_encoder_formula(self, indexer):
        """Index-time and query-time weighting must agree: the
        indexer's idf is the same function SparseEncoder applies when
        writing per-chunk sparse weights. If these two drift apart,
        BM25 scores stop being comparable across the two paths."""
        idx = indexer.build([
            make_record("a", "rare common"),
            make_record("b", "common beta"),
            make_record("c", "common gamma"),
        ])
        n = idx.n_docs
        for term, entry in idx.terms.items():
            df_t = len(entry.postings)
            # Formula as written in SparseEncoder._weights_for_doc.
            encoder_idf = math.log((n - df_t + 0.5) / (df_t + 0.5) + 1.0)
            assert entry.idf == pytest.approx(encoder_idf, rel=1e-9)

    def test_common_term_idf_is_never_negative(self, indexer):
        """A term in every doc has df=N. Under the raw BM25 form that
        yields a negative idf, which would make a common-term match
        *lower* a document's score. The ``+1`` form floors it at 0."""
        idx = indexer.build([
            make_record("a", "common alpha"),
            make_record("b", "common beta"),
            make_record("c", "common gamma"),
        ])
        # df=N=3 → log(0.5/3.5 + 1) ≈ 0.13, small but positive.
        assert idx.terms["common"].idf > 0

    def test_all_idfs_non_negative(self, indexer, sample_corpus):
        """No term in any corpus may produce a negative idf."""
        idx = indexer.build(sample_corpus)
        for term, entry in idx.terms.items():
            assert entry.idf >= 0, f"term {term!r} has negative idf"

    def test_rare_term_has_higher_idf_than_common(self, indexer):
        """Rarity still ranks above commonness — flooring at zero must
        not flatten the ordering between terms."""
        idx = indexer.build([
            make_record("a", "rare common"),
            make_record("b", "common beta"),
            make_record("c", "common gamma"),
        ])
        # "rare"   df=1/3 → log(2.5/1.5 + 1) ≈ 0.98
        # "common" df=3/3 → log(0.5/3.5 + 1) ≈ 0.13
        assert idx.terms["rare"].idf > 0
        assert idx.terms["common"].idf > 0
        assert idx.terms["rare"].idf > idx.terms["common"].idf


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------

class TestRoundtrip:
    def test_save_load_preserves_index(
        self, indexer, sample_corpus, tmp_path
    ):
        idx = indexer.build(sample_corpus)
        path = indexer.save(idx, "test")
        assert path.exists()
        # Default location check
        assert path.parent == tmp_path / "bm25"
        assert path.name == "test.json"

        loaded = indexer.load("test")
        assert loaded.n_docs == idx.n_docs
        assert loaded.avgdl == pytest.approx(idx.avgdl)
        assert loaded.k1 == idx.k1
        assert loaded.b == idx.b
        assert set(loaded.terms) == set(idx.terms)
        for term in idx.terms:
            le = loaded.terms[term]
            ie = idx.terms[term]
            assert le.idf == pytest.approx(ie.idf)
            assert len(le.postings) == len(ie.postings)
            for lp, ip in zip(le.postings, ie.postings):
                assert lp.chunk_id == ip.chunk_id
                assert lp.tf == ip.tf
                assert lp.doc_length == ip.doc_length

    def test_default_persist_dir_matches_spec(self):
        """Spec: '持久化到 data/db/bm25/'."""
        assert DEFAULT_PERSIST_DIR == "./data/db/bm25"

    def test_persist_dir_created_on_save(self, tmp_path):
        """save() must create the parent dir if it doesn't exist."""
        nested = tmp_path / "a" / "b" / "c"
        idxr = BM25Indexer(
            persist_dir=str(nested),
            sparse_encoder=SparseEncoder(),
        )
        idx = idxr.build([make_record("a", "hello")])
        path = idxr.save(idx, "x")
        assert path.exists()
        assert nested.is_dir()

    def test_load_missing_file_raises(self, indexer):
        with pytest.raises(FileNotFoundError, match="not found"):
            indexer.load("nonexistent")

    def test_query_after_load_returns_same_top_k(
        self, indexer, sample_corpus
    ):
        idx = indexer.build(sample_corpus)
        results_before = indexer.query(idx, "fox", top_k=3)

        indexer.save(idx, "rt")
        loaded = indexer.load("rt")
        results_after = indexer.query(loaded, "fox", top_k=3)

        assert [r.chunk_id for r in results_before] == [
            r.chunk_id for r in results_after
        ]
        for a, b in zip(results_before, results_after):
            assert a.score == pytest.approx(b.score, rel=1e-9)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class TestQuery:
    def test_query_accepts_string(self, indexer, sample_corpus):
        idx = indexer.build(sample_corpus)
        results = indexer.query(idx, "fox")
        assert all(isinstance(r, ScoredDoc) for r in results)

    def test_query_accepts_token_list(self, indexer, sample_corpus):
        idx = indexer.build(sample_corpus)
        results = indexer.query(idx, ["fox", "brown"])
        assert isinstance(results, list)

    def test_empty_query_returns_empty(self, indexer, sample_corpus):
        idx = indexer.build(sample_corpus)
        assert indexer.query(idx, "") == []
        assert indexer.query(idx, []) == []

    def test_query_with_no_matching_terms_returns_empty(
        self, indexer, sample_corpus
    ):
        idx = indexer.build(sample_corpus)
        # All-stopword query
        assert indexer.query(idx, "the of a") == []
        # Made-up term
        assert indexer.query(idx, "xyzzy") == []

    def test_query_returns_top_k_in_score_order(
        self, indexer, sample_corpus
    ):
        idx = indexer.build(sample_corpus)
        results = indexer.query(idx, "fox brown", top_k=5)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
        # Ties broken by chunk_id ascending (stable)
        for a, b in zip(results, results[1:]):
            if a.score == b.score:
                assert a.chunk_id < b.chunk_id

    def test_top_k_limit_respected(self, indexer, sample_corpus):
        idx = indexer.build(sample_corpus)
        results = indexer.query(idx, "the", top_k=2)
        assert len(results) <= 2

    def test_query_against_specific_term(self, indexer, sample_corpus):
        """Querying 'fox' should rank c1 (and maybe c4) above others."""
        idx = indexer.build(sample_corpus)
        results = indexer.query(idx, "fox", top_k=5)
        ids = [r.chunk_id for r in results]
        # c1 has 'fox' once; c4 has 'foxes' (after tokenization);
        # c5 has 'foxes'. Both contain a fox-related token.
        # The exact set depends on tokenization, but the first hit
        # should be one of c1/c4/c5.
        assert ids[0] in {"c1", "c4", "c5"}
        # No chunk without a fox-related token should appear
        assert "c3" not in ids  # c3 has no fox token

    def test_stopword_only_query_returns_empty(self, indexer):
        """Querying with only stopwords yields no token contributions → empty."""
        idx = indexer.build([
            make_record("a", "alpha beta"),
            make_record("b", "gamma delta"),
        ])
        # All English stopwords
        results = indexer.query(idx, "the of a")
        assert results == []

    def test_common_term_produces_non_negative_scores(self, indexer):
        """Under the ``+1`` IDF form, a term appearing in every doc
        still scores positively (just weakly). Raw BM25 would score it
        negatively, which inverts the ranking: a doc matching the query
        would rank *below* a doc matching nothing."""
        idx = indexer.build([
            make_record("a", "common alpha"),
            make_record("b", "common beta"),
            make_record("c", "common gamma"),
        ])
        # "common" is in all 3 docs → idf ≈ 0.13 → scores > 0
        results = indexer.query(idx, "common")
        assert results  # not empty — these are real BM25 scores
        assert all(r.score > 0 for r in results)

    def test_matching_doc_always_outranks_non_matching(self, indexer):
        """The practical guarantee the ``+1`` form buys us: a document
        that matches a query term can never score below one that does
        not match it at all (which scores 0)."""
        idx = indexer.build([
            make_record("a", "common alpha"),
            make_record("b", "common beta"),
            make_record("c", "common gamma"),
        ])
        results = indexer.query(idx, "common")
        # Every doc matches "common", so all must beat the 0 floor a
        # non-matching doc would get.
        assert all(r.score > 0 for r in results)
        # And a term nobody has returns nothing at all.
        assert indexer.query(idx, "absent") == []


# ---------------------------------------------------------------------------
# Incremental update
# ---------------------------------------------------------------------------

class TestIncremental:
    def test_add_new_chunks_grows_index(self, indexer, sample_corpus):
        idx = indexer.build(sample_corpus)
        n_before = idx.n_docs
        new_records = [
            make_record("c6", "the quick brown hawk soars"),
            make_record("c7", "the brown hawk perches"),
        ]
        indexer.add(idx, new_records)
        assert idx.n_docs == n_before + 2
        # New term "hawk" should be in the index now
        assert "hawk" in idx.terms
        # Existing term "brown" should have new postings
        brown = idx.terms["brown"]
        chunk_ids = {p.chunk_id for p in brown.postings}
        assert "c6" in chunk_ids
        assert "c7" in chunk_ids

    def test_add_replaces_existing_chunk(self, indexer, sample_corpus):
        """Re-adding a chunk_id with new text replaces (not duplicates)."""
        idx = indexer.build(sample_corpus)
        # c1 originally mentions 'fox' once
        assert "fox" in idx.terms
        assert any(p.chunk_id == "c1" for p in idx.terms["fox"].postings)

        # Re-add c1 with totally new content (no 'fox')
        indexer.add(idx, [make_record("c1", "completely new content zebra")])
        # c1 is no longer associated with 'fox' — and since c1 was
        # the only chunk with 'fox' in this corpus, the term itself
        # is dropped from the index (no postings → no point keeping
        # the entry).
        assert "fox" not in idx.terms
        # But 'zebra' is now a new term in the index
        assert "zebra" in idx.terms

    def test_add_updates_idf(self, indexer, sample_corpus):
        """idfs should change after adding docs that introduce new df."""
        idx = indexer.build(sample_corpus)
        old_idf_fox = idx.terms["fox"].idf
        # Add 2 more docs that contain 'fox'
        indexer.add(idx, [
            make_record("c6", "fox appears again"),
            make_record("c7", "another fox here"),
        ])
        # 'fox' is now in 4 docs instead of 1 → idf should drop
        new_idf_fox = idx.terms["fox"].idf
        assert new_idf_fox < old_idf_fox

    def test_add_empty_records_noop(self, indexer, sample_corpus):
        idx = indexer.build(sample_corpus)
        n_docs_before = idx.n_docs
        n_terms_before = len(idx.terms)
        indexer.add(idx, [])
        assert idx.n_docs == n_docs_before
        assert len(idx.terms) == n_terms_before

    def test_add_to_empty_index_equivalent_to_build(self, indexer):
        """Adding to a fresh index should produce the same result as building."""
        records = [
            make_record("a", "the quick brown fox"),
            make_record("b", "the lazy dog"),
        ]
        fresh_idx = indexer.build(records)

        empty_idx = BM25Index(k1=indexer.k1, b=indexer.b)
        indexer.add(empty_idx, records)

        assert empty_idx.n_docs == fresh_idx.n_docs
        assert set(empty_idx.terms) == set(fresh_idx.terms)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class TestConfig:
    def test_k1_negative_raises(self, tmp_path):
        with pytest.raises(ValueError, match="k1"):
            BM25Indexer(
                persist_dir=str(tmp_path),
                sparse_encoder=SparseEncoder(),
                k1=-1,
            )

    def test_b_out_of_range_raises(self, tmp_path):
        with pytest.raises(ValueError, match="b"):
            BM25Indexer(
                persist_dir=str(tmp_path),
                sparse_encoder=SparseEncoder(),
                b=1.5,
            )


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

class TestTrace:
    def test_trace_records_build_events(self, indexer, sample_corpus):
        trace = new_trace()
        indexer.build(sample_corpus, trace=trace)
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "bm25_indexer"
        ]
        assert "build_start" in events
        assert "build_finish" in events

    def test_trace_records_save_and_load(self, indexer, sample_corpus):
        idx = indexer.build(sample_corpus)
        trace = new_trace()
        indexer.save(idx, "x", trace=trace)
        loaded = indexer.load("x", trace=trace)
        assert loaded.n_docs == idx.n_docs
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "bm25_indexer"
        ]
        assert "save" in events
        assert "load" in events
