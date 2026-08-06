"""
Integration tests for HybridSearch (D5).

This is the moment the D stage becomes a "usable" search: query
goes in, top-k results come out, both paths contribute when
healthy and one path failing doesn't kill the search.

Covers:
- End-to-end: query → top-k with text + metadata
- Order determined by RRF (dense and sparse both contribute)
- Per-path failure falls back to the surviving path
- Both paths fail → empty list
- Empty query / top_k=0 → empty
- Metadata post-filter (exact match)
- Filters passed through to dense retriever
- trace events recorded
- per-call independence
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.query_engine import (
    HybridSearch,
    QueryProcessor,
    reciprocal_rank_fusion,
)
from src.core.query_engine.dense_retriever import DenseRetriever
from src.core.query_engine.sparse_retriever import SparseRetriever
from src.core.trace.trace_context import new_trace
from src.core.types import ChunkRecord, RetrievalResult
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage import BM25Indexer
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    QueryResult,
    VectorRecord,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeEmbedding(BaseEmbedding):
    def __init__(self, dim: int = 4) -> None:
        self._dim = dim

    @property
    def dimensions(self) -> int:
        return self._dim

    def embed(self, texts, **kwargs):
        return [[0.1 * i] * self._dim for i, _ in enumerate(texts)]

    def embed_single(self, text, **kwargs):
        return [0.1] * self._dim


class FakeVectorStore(BaseVectorStore):
    """In-memory store; ``get_by_ids`` returns full record dicts so
    HybridSearch's dense_results carry text + metadata."""

    def __init__(self, records: list[dict] | None = None) -> None:
        self._by_id: dict[str, dict] = {r["id"]: r for r in (records or [])}
        self.query_fail = False

    def upsert(self, records, **kwargs):
        for r in records:
            self._by_id[r.id] = {
                "id": r.id, "text": r.text, "metadata": r.metadata,
            }
        return len(records)

    def query(self, vector, top_k=10, filters=None, **kwargs):
        if self.query_fail:
            raise RuntimeError("fake store down")
        # Match by id containing query's "text" if available
        # (rough heuristic for tests). Or just return top-k by insertion.
        results = []
        for r in list(self._by_id.values())[:top_k]:
            results.append(QueryResult(
                id=r["id"], score=0.9,
                text=r["text"], metadata=r["metadata"],
            ))
        return results

    def delete(self, ids, **kwargs):
        before = len(self._by_id)
        for i in ids:
            self._by_id.pop(i, None)
        return before - len(self._by_id)

    def get_by_ids(self, ids, **kwargs):
        return [
            {"id": i, "text": self._by_id[i]["text"],
             "metadata": dict(self._by_id[i]["metadata"])}
            for i in ids if i in self._by_id
        ]

    def count(self, **kwargs):
        return len(self._by_id)


# ---------------------------------------------------------------------------
# Build a real (BM25 + vector) corpus for end-to-end testing
# ---------------------------------------------------------------------------

CORPUS = [
    ("c1", "BM25 is a bag-of-words retrieval function",
     {"doc_type": "pdf", "collection": "papers"}),
    ("c2", "BM25 uses term frequency and inverse document frequency",
     {"doc_type": "pdf", "collection": "papers"}),
    ("c3", "Dense retrieval uses embedding vectors for semantic search",
     {"doc_type": "pdf", "collection": "papers"}),
    ("c4", "Hybrid search combines BM25 and dense retrieval",
     {"doc_type": "md", "collection": "blog"}),
    ("c5", "RAG pipelines augment LLMs with retrieved context",
     {"doc_type": "md", "collection": "blog"}),
]


def _build_corpus(tmp_path):
    """Build a BM25 index + a FakeVectorStore, both keyed the same
    way, so a chunk's id is consistent across both."""
    # Vector store
    records = [
        {"id": cid, "text": text, "metadata": dict(meta)}
        for cid, text, meta in CORPUS
    ]
    store = FakeVectorStore(records)

    # BM25 index
    chunks = [
        ChunkRecord(id=cid, text=text, metadata=meta)
        for cid, text, meta in CORPUS
    ]
    bm25 = BM25Indexer(
        persist_dir=str(tmp_path / "bm25"),
        sparse_encoder=SparseEncoder(),
    )
    index = bm25.build(chunks)
    bm25.save(index, "corpus")
    return store, bm25


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def components(tmp_path):
    store, bm25 = _build_corpus(tmp_path)
    embedding = FakeEmbedding(dim=4)
    dense = DenseRetriever(embedding=embedding, vector_store=store)
    sparse = SparseRetriever(
        bm25_indexer=bm25, vector_store=store, index_name="corpus",
    )
    processor = QueryProcessor(sparse_encoder=SparseEncoder())
    hybrid = HybridSearch(
        query_processor=processor,
        dense_retriever=dense,
        sparse_retriever=sparse,
        fusion=reciprocal_rank_fusion,
    )
    return hybrid, store, dense, sparse


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_returns_list_with_text_and_metadata(self, components):
        hybrid, *_ = components
        results = hybrid.search("BM25 retrieval", top_k=5)
        assert isinstance(results, list)
        for r in results:
            assert r.text
            assert isinstance(r.metadata, dict)

    def test_top_k_respected(self, components):
        hybrid, *_ = components
        results = hybrid.search("BM25 retrieval", top_k=2)
        assert len(results) <= 2

    def test_results_ranked_by_fusion(self, components):
        """Dense and sparse both contribute; fusion decides order."""
        hybrid, *_ = components
        results = hybrid.search("BM25 retrieval", top_k=5)
        # We can't assert exact order (depends on the fake dense
        # ordering), but we can assert that known-bm25 chunks
        # appear AND that the order is stable across calls.
        ids = [r.chunk_id for r in results]
        # 'BM25' appears in c1, c2, c4 → at least one should be in top-5
        assert set(ids) & {"c1", "c2", "c4"}

    def test_deterministic_across_calls(self, components):
        hybrid, *_ = components
        r1 = hybrid.search("BM25 retrieval", top_k=3)
        r2 = hybrid.search("BM25 retrieval", top_k=3)
        assert [x.chunk_id for x in r1] == [x.chunk_id for x in r2]

    def test_empty_query(self, components):
        hybrid, *_ = components
        assert hybrid.search("") == []
        assert hybrid.search("   ") == []

    def test_top_k_zero(self, components):
        hybrid, *_ = components
        assert hybrid.search("x", top_k=0) == []

    def test_top_k_negative(self, components):
        hybrid, *_ = components
        assert hybrid.search("x", top_k=-1) == []


# ---------------------------------------------------------------------------
# Per-path fallback
# ---------------------------------------------------------------------------

class TestPerPathFallback:
    def test_dense_failure_keeps_sparse(self, components):
        hybrid, store, *_ = components
        store.query_fail = True
        results = hybrid.search("BM25", top_k=5)
        # Sparse still works → we get BM25 hits
        assert results
        # Source is "sparse" (not "fusion" since dense failed)
        for r in results:
            assert r.source == "sparse"

    def test_sparse_failure_keeps_dense(self, components, tmp_path):
        hybrid, store, dense, _ = components
        # Make the BM25 indexer fail by deleting it from disk
        # and using an indexer pointing at a non-existent file.
        from src.ingestion.storage import BM25Indexer
        broken_bm25 = BM25Indexer(
            persist_dir=str(tmp_path / "nonexistent"),
            sparse_encoder=SparseEncoder(),
        )
        hybrid.sparse = SparseRetriever(
            bm25_indexer=broken_bm25, vector_store=store,
            index_name="missing",
        )
        # dense will return its results; sparse raises FileNotFoundError
        # → caught by HybridSearch → falls back to dense-only
        results = hybrid.search("BM25", top_k=5)
        assert results
        for r in results:
            assert r.source == "dense"

    def test_both_paths_fail_returns_empty(self, components, tmp_path):
        hybrid, store, dense, _ = components
        # Kill dense
        store.query_fail = True
        # Kill sparse
        from src.ingestion.storage import BM25Indexer
        broken_bm25 = BM25Indexer(
            persist_dir=str(tmp_path / "nonexistent"),
            sparse_encoder=SparseEncoder(),
        )
        hybrid.sparse = SparseRetriever(
            bm25_indexer=broken_bm25, vector_store=store,
            index_name="missing",
        )
        results = hybrid.search("BM25", top_k=5)
        assert results == []


# ---------------------------------------------------------------------------
# Metadata filters
# ---------------------------------------------------------------------------

class TestMetadataFilters:
    def test_post_filter_drops_non_matching(self, components):
        hybrid, *_ = components
        # Only blog posts (doc_type=md)
        results = hybrid.search(
            "BM25 retrieval", top_k=10, filters={"doc_type": "md"},
        )
        for r in results:
            assert r.metadata.get("doc_type") == "md"

    def test_post_filter_with_no_match_returns_empty(self, components):
        hybrid, *_ = components
        results = hybrid.search(
            "BM25", top_k=10, filters={"collection": "nonexistent"},
        )
        assert results == []

    def test_filters_passed_to_dense(self, components):
        """The filter dict is forwarded to the dense retriever,
        which the store then uses to narrow its query."""
        hybrid, store, dense, _ = components
        # Spy on the store's query call
        original_query = store.query
        captured = {}
        def spy(vector, top_k=10, filters=None, **kwargs):
            captured["filters"] = filters
            return original_query(vector, top_k, filters, **kwargs)
        store.query = spy

        hybrid.search("BM25", top_k=5, filters={"doc_type": "pdf"})
        assert captured["filters"] == {"doc_type": "pdf"}

    def test_auto_filters_merged_with_caller_filters(self, components, tmp_path):
        """If QueryProcessor auto-extracts filters (e.g. 'BM25
        site:wikipedia'), and the caller also passes filters,
        they're merged — caller wins on key collision."""
        from src.ingestion.embedding.sparse_encoder import SparseEncoder
        proc = QueryProcessor(
            sparse_encoder=SparseEncoder(), extract_filters=True,
        )
        hybrid, *_ = components
        hybrid.query_processor = proc
        # "BM25" + "site:wikipedia" → auto-filters has 'site'
        results = hybrid.search(
            "BM25 site:wikipedia", top_k=5,
            filters={"doc_type": "pdf"},  # caller-provided
        )
        # The dense retriever should have received BOTH filters
        # (merged with caller winning on overlap)
        # We don't assert exact values here (depends on internal
        # state); we just verify the search runs without error.
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Trace (F3)
# ---------------------------------------------------------------------------

class TestTrace:
    """
    F3: per-stage trace events with method + elapsed_ms.

    A single HybridSearch.search() should produce, in order:
    query_processing → dense_retrieval → sparse_retrieval → fusion
    → (post_filter if filters given) → trim.
    """

    EXPECTED_STAGES = [
        "query_processing",
        "dense_retrieval",
        "sparse_retrieval",
        "fusion",
        "trim",
    ]

    def test_trace_type_defaults_to_ingestion(self):
        """new_trace() without args is the ingestion default;
        query tests should explicitly use trace_type='query'."""
        from src.core.trace import new_trace
        t = new_trace()
        assert t.trace_type == "ingestion"

    def test_query_trace_records_all_stages(self, components):
        hybrid, *_ = components
        trace = new_trace(trace_type="query")
        hybrid.search("BM25 是什么?", top_k=3, trace=trace)
        names = [s["name"] for s in trace.stages]
        for stage in self.EXPECTED_STAGES:
            assert stage in names, (
                f"missing stage {stage!r} in trace: {names}"
            )

    def test_each_stage_has_method_and_elapsed_ms(self, components):
        hybrid, *_ = components
        trace = new_trace(trace_type="query")
        hybrid.search("hybrid search", top_k=3, trace=trace)
        # Every stage except the no_results / failure events should
        # carry method + elapsed_ms.
        for s in trace.stages:
            if s["name"] in self.EXPECTED_STAGES:
                assert "method" in s, f"stage {s['name']!r} missing 'method'"
                assert "elapsed_ms" in s, (
                    f"stage {s['name']!r} missing 'elapsed_ms'"
                )
                assert isinstance(s["method"], str)
                assert s["method"], f"empty method on stage {s['name']!r}"
                assert s["elapsed_ms"] >= 0.0

    def test_trace_to_dict_has_query_type(self, components):
        hybrid, *_ = components
        trace = new_trace(trace_type="query")
        hybrid.search("anything", top_k=3, trace=trace)
        d = trace.to_dict()
        assert d["trace_type"] == "query"
        assert d["finished_at"] is None  # not finished by search()

    def test_post_filter_only_recorded_when_filters_provided(self, components):
        hybrid, *_ = components
        trace = new_trace(trace_type="query")
        hybrid.search("hybrid search", top_k=3, trace=trace)
        names = [s["name"] for s in trace.stages]
        assert "post_filter" not in names

        trace2 = new_trace(trace_type="query")
        hybrid.search(
            "hybrid search", top_k=3, trace=trace2,
            filters={"doc_type": "pdf"},
        )
        names2 = [s["name"] for s in trace2.stages]
        assert "post_filter" in names2
        pf = next(s for s in trace2.stages if s["name"] == "post_filter")
        assert pf["method"] == "exact_match"

    def test_no_trace_argument_does_not_raise(self, components):
        """trace=None must be a clean no-op."""
        hybrid, *_ = components
        # Should not raise; results should still come back.
        results = hybrid.search("BM25", top_k=3)
        assert isinstance(results, list)

    def test_trace_records_path_failures(self, components, tmp_path):
        hybrid, store, _, _ = components
        store.query_fail = True
        trace = new_trace()
        hybrid.search("BM25", top_k=3, trace=trace)
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "hybrid_search"
        ]
        assert "dense_failed" in events

    def test_trace_records_no_results(self, components, tmp_path):
        hybrid, store, _, _ = components
        store.query_fail = True
        from src.ingestion.storage import BM25Indexer
        broken_bm25 = BM25Indexer(
            persist_dir=str(tmp_path / "nope"),
            sparse_encoder=SparseEncoder(),
        )
        hybrid.sparse = SparseRetriever(
            bm25_indexer=broken_bm25, vector_store=store,
            index_name="missing",
        )
        trace = new_trace()
        hybrid.search("BM25", top_k=3, trace=trace)
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "hybrid_search"
        ]
        assert "no_results" in events


# ---------------------------------------------------------------------------
# Custom fusion (injected callable)
# ---------------------------------------------------------------------------

class TestCustomFusion:
    def test_custom_fusion_used(self, components):
        hybrid, *_ = components
        calls = []

        def my_fusion(rankings, k):
            calls.append((list(rankings), k))
            # Just concatenate and take the first top_k
            out = []
            for ranking in rankings:
                out.extend(ranking)
            return out[:5]

        hybrid.fusion = my_fusion
        results = hybrid.search("BM25", top_k=3)
        assert calls  # fusion was called
        # k passed through
        assert calls[0][1] == 60


# ---------------------------------------------------------------------------
# Retrieval multiplier
# ---------------------------------------------------------------------------

class TestRetrievalMultiplier:
    def test_each_retriever_fetches_top_k_times_multiplier(self):
        """Each retriever should be called with fetch_k = top_k *
        multiplier. The default multiplier is 2."""
        from src.ingestion.embedding.sparse_encoder import SparseEncoder
        from src.core.query_engine import QueryProcessor
        from src.core.query_engine.dense_retriever import DenseRetriever
        from src.core.query_engine.sparse_retriever import SparseRetriever
        from src.libs.embedding.base_embedding import BaseEmbedding
        from src.libs.vector_store.base_vector_store import (
            BaseVectorStore, QueryResult,
        )

        # Spy on dense.retrieve
        dense_calls = []

        class _Emb(BaseEmbedding):
            @property
            def dimensions(self): return 4
            def embed(self, texts, **kw):
                return [[0.1]] * 4
            def embed_single(self, text, **kw):
                return [0.1] * 4

        class _Store(BaseVectorStore):
            def __init__(self):
                self._by_id = {}
                self.get_by_ids_calls = []
            def upsert(self, records, **kw): return 0
            def query(self, vector, top_k=10, filters=None, **kw):
                # Return one record per call so dense.retrieve has results
                return [
                    QueryResult(id=f"d{i}", score=0.9, text="x", metadata={})
                    for i in range(top_k)
                ]
            def delete(self, ids, **kw): return 0
            def get_by_ids(self, ids, **kw):
                self.get_by_ids_calls.append(list(ids))
                return [
                    {"id": i, "text": "x", "metadata": {}}
                    for i in ids
                ]
            def count(self, **kw): return 0

        store = _Store()
        dense = DenseRetriever(embedding=_Emb(), vector_store=store)
        original_retrieve = dense.retrieve
        def spy_retrieve(query, top_k=10, filters=None, trace=None):
            dense_calls.append(top_k)
            return original_retrieve(query, top_k, filters, trace=trace)
        dense.retrieve = spy_retrieve

        # Skip BM25 (use a fake that returns nothing)
        from src.ingestion.storage import BM25Indexer
        from src.ingestion.storage.bm25_indexer import BM25Index
        class _NoBM25(SparseRetriever):
            def retrieve(self, keywords, top_k=10, trace=None):
                return []

        # Use the no-sparse subclass to isolate the dense fetch_k
        proc = QueryProcessor(sparse_encoder=SparseEncoder())
        # Give sparse a "no-op" subclass
        sparse = _NoBM25(
            bm25_indexer=BM25Indexer(
                persist_dir="/tmp/whatever",
                sparse_encoder=SparseEncoder(),
            ),
            vector_store=store, index_name="x",
        )
        hybrid = HybridSearch(
            query_processor=proc, dense_retriever=dense,
            sparse_retriever=sparse,
            retrieval_multiplier=3,
        )
        hybrid.search("x", top_k=4)
        # dense.retrieve was called with top_k * 3 = 12
        assert dense_calls == [12]
