"""
Unit tests for DenseRetriever (D2).

Covers:
- Basic retrieve: embed → query → list of RetrievalResult
- Returns ``RetrievalResult`` with all required fields (chunk_id,
  score, text, metadata, rank, source="dense")
- Empty / whitespace query → empty result
- top_k limit respected
- filters dict passed through to vector store
- Order preserved from vector store (which is already score-sorted)
- Embedding called once with the query
- Vector store called with the embedded vector
- Embedding error propagates
- Vector store error propagates
- Empty embedding result → empty list (no crash)
- Result count matches top_k
- Trace integration
- Per-call independence
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.query_engine import DenseRetriever
from src.core.trace.trace_context import new_trace
from src.core.types import ChunkRecord, RetrievalResult
from src.libs.embedding.base_embedding import (
    BaseEmbedding,
    EmbeddingError,
)
from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    QueryResult,
    VectorStoreError,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeEmbedding(BaseEmbedding):
    def __init__(self, dim: int = 4, fail: bool = False) -> None:
        self._dim = dim
        self.embed_single_calls: list[str] = []
        self.fail = fail

    @property
    def dimensions(self) -> int:
        return self._dim

    def embed(self, texts, **kwargs):
        if self.fail:
            raise EmbeddingError("fake upstream down")
        return [[0.1 * (i + 1)] * self._dim for i, _ in enumerate(texts)]

    def embed_single(self, text: str, **kwargs):
        self.embed_single_calls.append(text)
        if self.fail:
            raise EmbeddingError("fake upstream down")
        return [0.1] * self._dim


class FakeVectorStore(BaseVectorStore):
    def __init__(
        self,
        results: list[QueryResult] | None = None,
        fail: bool = False,
    ) -> None:
        self.query_calls: list[tuple] = []
        self._results = results or []
        self.fail = fail

    def upsert(self, records, **kwargs):
        return 0

    def query(self, vector, top_k=10, filters=None, **kwargs):
        self.query_calls.append((list(vector), top_k, dict(filters or {})))
        if self.fail:
            raise VectorStoreError("fake store down")
        # Honor top_k — slice our canned results
        return self._results[:top_k]

    def delete(self, ids, **kwargs):
        return 0

    def get_by_ids(self, ids, **kwargs):
        return []

    def count(self, **kwargs):
        return len(self._results)


def make_hit(
    hit_id: str, score: float = 0.9, text: str = "x", metadata=None
) -> QueryResult:
    return QueryResult(
        id=hit_id, score=score, text=text,
        metadata=metadata or {"source": "test"},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def embedding() -> FakeEmbedding:
    return FakeEmbedding(dim=8)


@pytest.fixture
def store() -> FakeVectorStore:
    return FakeVectorStore(
        results=[
            make_hit("a", score=0.95, text="alpha text", metadata={"src": "a"}),
            make_hit("b", score=0.85, text="beta text", metadata={"src": "b"}),
            make_hit("c", score=0.75, text="gamma text", metadata={"src": "c"}),
        ],
    )


@pytest.fixture
def retriever(embedding, store) -> DenseRetriever:
    return DenseRetriever(embedding=embedding, vector_store=store)


# ---------------------------------------------------------------------------
# Basic retrieval
# ---------------------------------------------------------------------------

class TestBasic:
    def test_returns_list(self, retriever):
        out = retriever.retrieve("hello world")
        assert isinstance(out, list)
        assert all(isinstance(r, RetrievalResult) for r in out)

    def test_count_matches_top_k(self, retriever):
        out = retriever.retrieve("x", top_k=2)
        assert len(out) == 2

    def test_results_contain_text_and_metadata(self, retriever):
        out = retriever.retrieve("x", top_k=3)
        for r in out:
            assert r.text
            assert isinstance(r.metadata, dict)

    def test_results_have_dense_source(self, retriever):
        out = retriever.retrieve("x", top_k=2)
        for r in out:
            assert r.source == "dense"

    def test_chunk_id_accessible(self, retriever):
        out = retriever.retrieve("x", top_k=3)
        ids = [r.chunk_id for r in out]
        assert ids == ["a", "b", "c"]

    def test_score_passed_through(self, retriever):
        out = retriever.retrieve("x", top_k=3)
        scores = [r.score for r in out]
        assert scores == [0.95, 0.85, 0.75]

    def test_rank_assigned_sequentially(self, retriever):
        out = retriever.retrieve("x", top_k=3)
        assert [r.rank for r in out] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Wiring / orchestration
# ---------------------------------------------------------------------------

class TestWiring:
    def test_embedding_called_once_with_query(self, retriever, embedding):
        retriever.retrieve("the actual query string")
        assert embedding.embed_single_calls == ["the actual query string"]

    def test_vector_store_called_with_embedded_vector(
        self, retriever, store, embedding
    ):
        retriever.retrieve("x", top_k=5)
        # The vector the store receives should match what the
        # embedding produced (same instance, since FakeEmbedding
        # returns the same list every time — but we just verify
        # the length matches dimensions).
        assert len(store.query_calls) == 1
        sent_vector, sent_top_k, _ = store.query_calls[0]
        assert len(sent_vector) == embedding.dimensions
        assert sent_top_k == 5

    def test_filters_passed_through(self, retriever, store):
        retriever.retrieve("x", top_k=10, filters={"doc_type": "pdf"})
        assert store.query_calls[0][2] == {"doc_type": "pdf"}

    def test_filters_default_empty_dict(self, retriever, store):
        retriever.retrieve("x", top_k=10)
        # No filters → empty dict passed to store (not None)
        assert store.query_calls[0][2] == {}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_query_returns_empty(self, retriever, embedding, store):
        out = retriever.retrieve("")
        assert out == []
        # Embedding / store should NOT be called
        assert embedding.embed_single_calls == []
        assert store.query_calls == []

    def test_whitespace_query_returns_empty(self, retriever, embedding, store):
        out = retriever.retrieve("   \n\t  ")
        assert out == []
        assert embedding.embed_single_calls == []
        assert store.query_calls == []

    def test_top_k_zero_returns_empty(self, retriever):
        out = retriever.retrieve("x", top_k=0)
        assert out == []

    def test_top_k_negative_returns_empty(self, retriever):
        out = retriever.retrieve("x", top_k=-1)
        assert out == []

    def test_empty_store_results(self, retriever):
        # Store with no results → empty list
        retriever.vector_store._results = []
        out = retriever.retrieve("x")
        assert out == []

    def test_result_with_missing_text(self, retriever, store):
        """If the store returns a hit with empty text, the retriever
        must still produce a result (graceful degradation)."""
        store._results = [
            QueryResult(id="x", score=0.5, text="", metadata={}),
        ]
        out = retriever.retrieve("q")
        assert len(out) == 1
        assert out[0].text == ""
        assert out[0].chunk_id == "x"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_embedding_error_propagates(self, embedding, store):
        embedding.fail = True
        r = DenseRetriever(embedding=embedding, vector_store=store)
        with pytest.raises(EmbeddingError, match="upstream down"):
            r.retrieve("x")

    def test_store_error_propagates(self, embedding, store):
        store.fail = True
        r = DenseRetriever(embedding=embedding, vector_store=store)
        with pytest.raises(VectorStoreError, match="store down"):
            r.retrieve("x")

    def test_embedding_error_aborts_before_store(self, embedding, store):
        """If embedding fails, we must NOT call the store (no
        point querying an empty / nonsense vector)."""
        embedding.fail = True
        r = DenseRetriever(embedding=embedding, vector_store=store)
        with pytest.raises(EmbeddingError):
            r.retrieve("x")
        assert store.query_calls == []


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

class TestTrace:
    def test_trace_records_lifecycle(self, retriever):
        trace = new_trace()
        retriever.retrieve("x", top_k=3, trace=trace)
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "dense_retriever"
        ]
        assert "start" in events
        assert "finish" in events

    def test_trace_records_store_error(self, embedding, store):
        store.fail = True
        r = DenseRetriever(embedding=embedding, vector_store=store)
        trace = new_trace()
        with pytest.raises(VectorStoreError):
            r.retrieve("x", trace=trace)
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "dense_retriever"
        ]
        assert "store_error" in events

    def test_trace_records_embedding_error(self, embedding, store):
        embedding.fail = True
        r = DenseRetriever(embedding=embedding, vector_store=store)
        trace = new_trace()
        with pytest.raises(EmbeddingError):
            r.retrieve("x", trace=trace)
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "dense_retriever"
        ]
        assert "embedding_error" in events


# ---------------------------------------------------------------------------
# Per-call independence
# ---------------------------------------------------------------------------

class TestPerCall:
    def test_two_calls_dont_share_state(self, retriever):
        out1 = retriever.retrieve("q1")
        out2 = retriever.retrieve("q2")
        # Each call should produce the same store results
        # (FakeEmbedding returns the same vector regardless of
        # query text), so the output should be the same.
        assert [r.chunk_id for r in out1] == [r.chunk_id for r in out2]
        # But the embedding was called once per retrieve
        assert len(retriever.embedding.embed_single_calls) == 2
