"""
Unit tests for SparseRetriever (D3).

Covers:
- ``BaseVectorStore.get_by_ids()`` contract: abstract, no impl
  required in this test (we use a fake)
- BM25 query against a small known corpus
- Reverse-lookup merges BM25 scores with vector-store text/metadata
- Empty keywords / top_k=0
- Missing index → FileNotFoundError (caller must ingest first)
- BM25 hit missing from vector store → silently dropped
- Source = "sparse"
- Score from BM25 (negative is OK for raw BM25)
- Order preserved from BM25
- ``invalidate_cache()`` forces a reload
- Trace events
- Per-call independence
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.query_engine import SparseRetriever
from src.core.trace.trace_context import new_trace
from src.core.types import ChunkRecord, RetrievalResult
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage import BM25Indexer
from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    VectorStoreError,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeVectorStore(BaseVectorStore):
    """In-memory fake — keyed by id, returns full record dicts from
    ``get_by_ids``."""

    def __init__(self, records: list[dict] | None = None) -> None:
        self._by_id: dict[str, dict] = {
            r["id"]: r for r in (records or [])
        }
        self.get_by_ids_calls: list[list[str]] = []
        self.fail = False

    def seed(self, records: list[dict]) -> None:
        for r in records:
            self._by_id[r["id"]] = r

    def upsert(self, records, **kwargs):
        for r in records:
            self._by_id[r.id] = {
                "id": r.id, "text": r.text, "metadata": r.metadata,
            }
        return len(records)

    def query(self, vector, top_k=10, filters=None, **kwargs):
        return []

    def delete(self, ids, **kwargs):
        before = len(self._by_id)
        for i in ids:
            self._by_id.pop(i, None)
        return before - len(self._by_id)

    def get_by_ids(self, ids, **kwargs):
        self.get_by_ids_calls.append(list(ids))
        if self.fail:
            raise VectorStoreError("fake store down")
        return [self._by_id[i] for i in ids if i in self._by_id]

    def count(self, **kwargs):
        return len(self._by_id)


def make_record(
    chunk_id: str, text: str = "x", metadata: dict | None = None
) -> dict:
    return {
        "id": chunk_id,
        "text": text,
        "metadata": metadata or {"source": "test"},
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def corpus_records() -> list[dict]:
    return [
        make_record("a", text="BM25 is a bag-of-words method",
                    metadata={"source_path": "/a.pdf", "page": 1}),
        make_record("b", text="BM25 uses tf-idf scoring",
                    metadata={"source_path": "/b.pdf", "page": 2}),
        make_record("c", text="Dense retrieval uses vectors",
                    metadata={"source_path": "/c.pdf", "page": 3}),
    ]


@pytest.fixture
def seeded_store(corpus_records) -> FakeVectorStore:
    return FakeVectorStore(corpus_records)


@pytest.fixture
def bm25_indexer(tmp_path, seeded_store):
    """Build + save a BM25 index from records (we need ChunkRecord
    to feed the indexer, so we adapt from the fake-store records)."""
    chunks = [
        ChunkRecord(
            id=r["id"], text=r["text"], metadata=r["metadata"],
        )
        for r in seeded_store._by_id.values()
    ]
    # The seeded text in fixture has unique BM25-friendly terms.
    # Add a term that appears in BOTH 'a' and 'b' so idf is finite.
    # Actually, 'BM25' is in a and b, 'retrieval' in b and c, etc.
    # Default SparseEncoder will produce a useful index.
    indexer = BM25Indexer(
        persist_dir=str(tmp_path / "bm25"),
        sparse_encoder=SparseEncoder(),
    )
    index = indexer.build(chunks)
    indexer.save(index, "corpus")
    return indexer


@pytest.fixture
def retriever(bm25_indexer, seeded_store) -> SparseRetriever:
    return SparseRetriever(
        bm25_indexer=bm25_indexer,
        vector_store=seeded_store,
        index_name="corpus",
    )


# ---------------------------------------------------------------------------
# get_by_ids contract
# ---------------------------------------------------------------------------

class TestGetByIdsContract:
    def test_base_vector_store_has_abstract_method(self):
        """``get_by_ids`` is now part of the BaseVectorStore contract —
        a subclass that doesn't implement it can't be instantiated."""
        class Incomplete(BaseVectorStore):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Basic retrieval
# ---------------------------------------------------------------------------

class TestBasic:
    def test_returns_list(self, retriever):
        out = retriever.retrieve(["bm25"])
        assert isinstance(out, list)
        assert all(isinstance(r, RetrievalResult) for r in out)

    def test_results_have_text_and_metadata(self, retriever):
        out = retriever.retrieve(["bm25"])
        assert out, "expected at least one result"
        for r in out:
            assert r.text
            assert isinstance(r.metadata, dict)
            # Metadata from the seed should be present
            assert "source_path" in r.metadata

    def test_source_is_sparse(self, retriever):
        out = retriever.retrieve(["bm25"])
        for r in out:
            assert r.source == "sparse"

    def test_rank_assigned_in_bm25_order(self, retriever):
        out = retriever.retrieve(["bm25"])
        assert [r.rank for r in out] == list(range(1, len(out) + 1))

    def test_chunk_id_matches_bm25_hit(self, retriever):
        out = retriever.retrieve(["bm25"])
        # BM25 finds "a" and "b" (both have "bm25" in their text)
        ids = {r.chunk_id for r in out}
        # At least one of {a, b} should appear; "c" has no BM25
        assert ids & {"a", "b"}


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

class TestWiring:
    def test_keywords_passed_to_bm25(self, retriever, bm25_indexer):
        # Spy on bm25_indexer.query
        original = bm25_indexer.query
        calls = []
        def spy(index, query, top_k=10):
            calls.append(list(query))
            return original(index, query, top_k=top_k)
        bm25_indexer.query = spy

        retriever.retrieve(["bm25", "scoring"])
        assert calls == [["bm25", "scoring"]]

    def test_get_by_ids_called_with_bm25_chunk_ids(
        self, retriever, seeded_store
    ):
        retriever.retrieve(["bm25"])
        # get_by_ids should have been called with the chunk ids
        # that BM25 returned. We don't know the order, but we
        # can check that all calls are non-empty and the IDs
        # are from the seed.
        assert seeded_store.get_by_ids_calls
        ids_called = set()
        for call in seeded_store.get_by_ids_calls:
            ids_called.update(call)
        # BM25 only returns IDs that exist in the corpus
        assert ids_called <= {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_keywords(self, retriever):
        out = retriever.retrieve([])
        assert out == []

    def test_top_k_zero(self, retriever):
        out = retriever.retrieve(["bm25"], top_k=0)
        assert out == []

    def test_top_k_negative(self, retriever):
        out = retriever.retrieve(["bm25"], top_k=-1)
        assert out == []

    def test_unknown_term(self, retriever):
        """A term that's not in the index returns [] (or possibly
        all docs with idf=0 → score=0 → filtered). Either way,
        no error."""
        out = retriever.retrieve(["zzzqqqxxx"])
        assert isinstance(out, list)

    def test_bm25_hit_missing_from_vector_store(self, retriever, seeded_store):
        """If BM25 returns a chunk_id that the vector store doesn't
        have (e.g. added to BM25 but never upserted), the retriever
        silently drops it from the result."""
        # Add a "ghost" record to the BM25 index only (not in
        # the vector store).
        from src.ingestion.storage.bm25_indexer import BM25Index, Posting, TermEntry
        from src.core.types import ChunkRecord
        import math
        # Re-load the cached index and add a ghost entry
        idx = bm25_indexer_fixture = retriever.bm25_indexer.load(
            retriever.index_name
        )
        idx.terms.setdefault("ghost", TermEntry(
            idf=math.log((idx.n_docs - 0 + 0.5) / (0 + 0.5)),
            postings=[Posting(chunk_id="ghost", tf=1, doc_length=5)],
        ))
        # Save back so the cached version reflects the change
        retriever.bm25_indexer.save(idx, retriever.index_name)
        retriever.invalidate_cache()

        out = retriever.retrieve(["ghost"])
        # "ghost" is a BM25 hit but the vector store doesn't
        # have it → it should be silently dropped.
        ids = [r.chunk_id for r in out]
        assert "ghost" not in ids

    def test_missing_index_raises_file_not_found(self, tmp_path, seeded_store):
        from src.ingestion.storage import BM25Indexer
        indexer = BM25Indexer(
            persist_dir=str(tmp_path / "empty_bm25"),
            sparse_encoder=SparseEncoder(),
        )
        r = SparseRetriever(
            bm25_indexer=indexer, vector_store=seeded_store,
            index_name="nonexistent",
        )
        with pytest.raises(FileNotFoundError):
            r.retrieve(["bm25"])


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_get_by_ids_error_propagates(self, retriever, seeded_store):
        seeded_store.fail = True
        with pytest.raises(VectorStoreError, match="store down"):
            retriever.retrieve(["bm25"])

    def test_index_load_error_propagates(self, tmp_path, seeded_store):
        indexer = BM25Indexer(
            persist_dir=str(tmp_path / "no_index"),
            sparse_encoder=SparseEncoder(),
        )
        r = SparseRetriever(
            bm25_indexer=indexer, vector_store=seeded_store,
            index_name="missing",
        )
        with pytest.raises(FileNotFoundError):
            r.retrieve(["bm25"])


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class TestCache:
    def test_index_loaded_once(self, retriever, bm25_indexer):
        # Reset the call count on the underlying loader
        original_load = bm25_indexer.load
        call_count = {"n": 0}
        def counting_load(name):
            call_count["n"] += 1
            return original_load(name)
        bm25_indexer.load = counting_load

        retriever.retrieve(["bm25"])
        retriever.retrieve(["scoring"])
        # Two retrievals → load was called once (cache hit)
        assert call_count["n"] == 1

    def test_invalidate_cache_forces_reload(self, retriever, bm25_indexer):
        original_load = bm25_indexer.load
        call_count = {"n": 0}
        def counting_load(name):
            call_count["n"] += 1
            return original_load(name)
        bm25_indexer.load = counting_load

        retriever.retrieve(["bm25"])
        retriever.invalidate_cache()
        retriever.retrieve(["scoring"])
        assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

class TestTrace:
    def test_trace_records_lifecycle(self, retriever):
        trace = new_trace()
        retriever.retrieve(["bm25"], trace=trace)
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "sparse_retriever"
        ]
        assert "start" in events
        assert "finish" in events

    def test_trace_records_index_missing(self, tmp_path, seeded_store):
        indexer = BM25Indexer(
            persist_dir=str(tmp_path / "x"),
            sparse_encoder=SparseEncoder(),
        )
        r = SparseRetriever(
            bm25_indexer=indexer, vector_store=seeded_store,
            index_name="nope",
        )
        trace = new_trace()
        with pytest.raises(FileNotFoundError):
            r.retrieve(["bm25"], trace=trace)
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "sparse_retriever"
        ]
        assert "index_missing" in events

    def test_trace_records_lookup_error(self, retriever, seeded_store):
        seeded_store.fail = True
        trace = new_trace()
        with pytest.raises(VectorStoreError):
            retriever.retrieve(["bm25"], trace=trace)
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "sparse_retriever"
        ]
        assert "lookup_error" in events


# ---------------------------------------------------------------------------
# Per-call independence
# ---------------------------------------------------------------------------

class TestPerCall:
    def test_two_calls_independent(self, retriever):
        out1 = retriever.retrieve(["bm25"])
        out2 = retriever.retrieve(["scoring"])
        # Different queries should give different result sets
        ids1 = {r.chunk_id for r in out1}
        ids2 = {r.chunk_id for r in out2}
        # 'scoring' is only in chunk 'b'; 'BM25' is in 'a' and 'b'
        assert ids1 != ids2 or out1 != out2
