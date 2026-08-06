"""
Idempotency + contract tests for VectorUpserter (C12).

Covers:
- ID stability: same chunk → same id
- ID change on content / source_path / chunk_index change
- Batch upsert + order preservation
- Idempotency: re-upserting the same record → same id (store sees
  the same id both times)
- VectorRecord shape: id, vector, text, metadata passed through
- Empty input: 0 returned, no store call
- Validation: record without dense_vector → ValueError
- Store error propagation
- Trace integration
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.trace.trace_context import new_trace
from src.core.types import ChunkRecord
from src.ingestion.storage import VectorUpserter
from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    QueryResult,
    VectorRecord,
    VectorStoreError,
)


# ---------------------------------------------------------------------------
# Fake store
# ---------------------------------------------------------------------------

class FakeVectorStore(BaseVectorStore):
    """
    In-memory fake — records are appended to ``self.received`` and
    can be queried back. ``upsert`` is idempotent in the sense that
    the same id overwrites an earlier record (preserving test
    expectations about dedup).
    """

    def __init__(self) -> None:
        self.received: list[VectorRecord] = []
        self.upsert_calls: list[list[VectorRecord]] = []
        self._fail_on_upsert: Exception | None = None

    def upsert(self, records, **kwargs):
        if self._fail_on_upsert is not None:
            raise self._fail_on_upsert
        self.upsert_calls.append(list(records))
        # Idempotent: dedup by id, last write wins.
        by_id: dict[str, VectorRecord] = {
            r.id: r for r in self.received
        }
        for r in records:
            by_id[r.id] = r
        self.received = list(by_id.values())
        return len(records)

    def query(self, vector, top_k=10, filters=None, **kwargs):
        return []

    def delete(self, ids, **kwargs):
        before = {r.id for r in self.received}
        survivors = [r for r in self.received if r.id not in set(ids)]
        deleted = len(self.received) - len(survivors)
        self.received = survivors
        return deleted

    def get_by_ids(self, ids, **kwargs):
        # Reconstruct the {id, text, metadata} dict from VectorRecord.
        return [
            {"id": r.id, "text": r.text, "metadata": dict(r.metadata)}
            for r in self.received if r.id in set(ids)
        ]

    def set_fail(self, exc: Exception) -> None:
        self._fail_on_upsert = exc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store() -> FakeVectorStore:
    return FakeVectorStore()


@pytest.fixture
def upserter(store) -> VectorUpserter:
    return VectorUpserter(store)


def make_record(
    chunk_id: str = "c1",
    text: str = "hello world",
    vector: list[float] | None = None,
    source_path: str = "/tmp/x.pdf",
    chunk_index: int = 0,
) -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        text=text,
        metadata={"source_path": source_path, "chunk_index": chunk_index},
        start_offset=0,
        end_offset=len(text),
        # If the caller passes None (or omits the arg), the record
        # gets a placeholder 3-dim vector — the vector contents are
        # not the point of most tests, just that there IS a vector.
        # Use :func:`make_record_no_vec` to test the "no dense
        # vector" validation path.
        dense_vector=vector if vector is not None else [0.1, 0.2, 0.3],
    )


def make_record_no_vec(
    chunk_id: str = "c1",
    text: str = "hello world",
    source_path: str = "/tmp/x.pdf",
    chunk_index: int = 0,
) -> ChunkRecord:
    """Build a record with ``dense_vector=None`` for validation tests."""
    return ChunkRecord(
        id=chunk_id,
        text=text,
        metadata={"source_path": source_path, "chunk_index": chunk_index},
        start_offset=0,
        end_offset=len(text),
        dense_vector=None,
    )


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------

class TestBasic:
    def test_empty_input_returns_zero_no_store_call(self, upserter, store):
        n = upserter.upsert([])
        assert n == 0
        assert store.received == []
        assert store.upsert_calls == []

    def test_single_record_upserted(self, upserter, store):
        rec = make_record(vector=[0.1, 0.2, 0.3])
        n = upserter.upsert([rec])
        assert n == 1
        assert len(store.received) == 1
        assert store.received[0].vector == [0.1, 0.2, 0.3]

    def test_count_returned_matches_input(self, upserter):
        records = [make_record(chunk_id=f"c{i}", vector=[float(i)]*3)
                   for i in range(5)]
        n = upserter.upsert(records)
        assert n == 5


# ---------------------------------------------------------------------------
# ID stability (the spec's headline acceptance)
# ---------------------------------------------------------------------------

class TestIdStability:
    def test_same_record_produces_same_id(self):
        """The headline acceptance criterion: '同一 chunk 两次 upsert
        产生相同 id'."""
        rec = make_record()
        id1 = VectorUpserter.make_id(rec)
        id2 = VectorUpserter.make_id(rec)
        assert id1 == id2

    def test_id_format(self):
        rec = make_record()
        cid = VectorUpserter.make_id(rec)
        # 16 hex chars
        assert len(cid) == 16
        assert all(c in "0123456789abcdef" for c in cid)

    def test_re_upsert_same_record_uses_same_id(self, upserter, store):
        """The same chunk fed twice → same id in both store calls."""
        rec = make_record()
        upserter.upsert([rec])
        upserter.upsert([rec])
        assert len(store.upsert_calls) == 2
        id_first_call = store.upsert_calls[0][0].id
        id_second_call = store.upsert_calls[1][0].id
        assert id_first_call == id_second_call

    def test_re_upsert_overwrites_in_store(self, upserter, store):
        """Same id → store replaces; only ONE record remains in the
        fake store after the second call (this is the idempotency
        the spec asks for)."""
        rec = make_record(vector=[0.1, 0.2, 0.3])
        upserter.upsert([rec])
        upserter.upsert([rec])  # second call with same id
        assert len(store.received) == 1

    def test_id_independent_of_chunk_id_field(self):
        """The generated id is content-derived, NOT the chunk's own
        ``id`` field. Two chunks with identical content but different
        ``id`` fields should produce the same upsert id."""
        a = make_record(chunk_id="alpha", text="same text", chunk_index=0)
        b = make_record(chunk_id="beta", text="same text", chunk_index=0)
        assert VectorUpserter.make_id(a) == VectorUpserter.make_id(b)


# ---------------------------------------------------------------------------
# ID changes when content / source / index change
# ---------------------------------------------------------------------------

class TestIdChanges:
    def test_content_change_changes_id(self):
        a = make_record(text="hello world")
        b = make_record(text="hello WORLD!")  # tiny edit
        assert VectorUpserter.make_id(a) != VectorUpserter.make_id(b)

    def test_source_path_change_changes_id(self):
        a = make_record(source_path="/a.pdf")
        b = make_record(source_path="/b.pdf")
        assert VectorUpserter.make_id(a) != VectorUpserter.make_id(b)

    def test_chunk_index_change_changes_id(self):
        a = make_record(chunk_index=0)
        b = make_record(chunk_index=1)
        assert VectorUpserter.make_id(a) != VectorUpserter.make_id(b)

    def test_only_text_differs(self):
        """The id function uses (source_path, chunk_index, content)."""
        a = make_record(text="foo", source_path="/x", chunk_index=7)
        b = make_record(text="bar", source_path="/x", chunk_index=7)
        assert VectorUpserter.make_id(a) != VectorUpserter.make_id(b)

    def test_id_stable_with_missing_metadata(self):
        """If metadata lacks source_path / chunk_index, the id is
        still deterministic (uses empty/0 defaults)."""
        r1 = ChunkRecord(
            id="c1", text="x", metadata={}, dense_vector=[0.0],
        )
        r2 = ChunkRecord(
            id="c1", text="x", metadata={}, dense_vector=[0.0],
        )
        assert VectorUpserter.make_id(r1) == VectorUpserter.make_id(r2)

    def test_different_text_same_other_fields(self):
        r1 = make_record(text="foo", source_path="/a.pdf", chunk_index=0)
        r2 = make_record(text="bar", source_path="/a.pdf", chunk_index=0)
        assert VectorUpserter.make_id(r1) != VectorUpserter.make_id(r2)


# ---------------------------------------------------------------------------
# Batch + order
# ---------------------------------------------------------------------------

class TestBatch:
    def test_batch_preserves_order_in_store_call(self, upserter, store):
        records = [make_record(chunk_id=f"c{i}", text=f"text {i}")
                   for i in range(5)]
        upserter.upsert(records)
        # Single store call with all 5 records in input order
        assert len(store.upsert_calls) == 1
        call = store.upsert_calls[0]
        assert len(call) == 5
        # Each record's text matches the input position
        for i, vr in enumerate(call):
            assert vr.text == f"text {i}"

    def test_batch_count_returned_matches(self, upserter):
        records = [make_record(chunk_id=f"c{i}", vector=[float(i)]*3)
                   for i in range(10)]
        n = upserter.upsert(records)
        assert n == 10


# ---------------------------------------------------------------------------
# VectorRecord shape
# ---------------------------------------------------------------------------

class TestVectorRecordShape:
    def test_vector_passed_through(self, upserter, store):
        rec = make_record(vector=[0.1, 0.2, 0.3, 0.4])
        upserter.upsert([rec])
        assert store.received[0].vector == [0.1, 0.2, 0.3, 0.4]

    def test_text_passed_through(self, upserter, store):
        rec = make_record(text="the actual chunk content")
        upserter.upsert([rec])
        assert store.received[0].text == "the actual chunk content"

    def test_metadata_passed_through_and_extended(self, upserter, store):
        rec = make_record(
            source_path="/data/foo.pdf",
            chunk_index=3,
        )
        rec.metadata["doc_type"] = "pdf"  # additional field
        upserter.upsert([rec])
        meta = store.received[0].metadata
        assert meta["source_path"] == "/data/foo.pdf"
        assert meta["chunk_index"] == 3
        assert meta["doc_type"] == "pdf"
        # Original chunk id is surfaced (for join back from store)
        assert meta["chunk_id"] == "c1"

    def test_id_in_vector_record_matches_chunk_id(self, upserter, store):
        rec = make_record()
        upserter.upsert([rec])
        assert store.received[0].id == rec.id


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_record_without_dense_vector_raises(self, upserter):
        rec = make_record_no_vec()
        with pytest.raises(ValueError, match="dense_vector"):
            upserter.upsert([rec])

    def test_one_missing_dense_vector_raises(self, upserter):
        """Even one bad apple in the batch aborts — fail fast before
        partial write."""
        good = make_record(vector=[0.1], chunk_id="good")
        bad = make_record_no_vec(chunk_id="bad")
        with pytest.raises(ValueError, match="bad"):
            upserter.upsert([good, bad])

    def test_store_error_propagates(self, upserter, store):
        store.set_fail(VectorStoreError("connection refused"))
        rec = make_record()
        with pytest.raises(VectorStoreError, match="connection refused"):
            upserter.upsert([rec])


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

class TestTrace:
    def test_trace_records_start_and_finish(self, upserter, store):
        trace = new_trace()
        upserter.upsert(
            [make_record(chunk_id=f"c{i}") for i in range(3)],
            trace=trace,
        )
        events = [s.get("event") for s in trace.stages
                  if s.get("name") == "vector_upserter"]
        assert "start" in events
        assert "finish" in events

    def test_trace_records_error_on_store_failure(self, upserter, store):
        store.set_fail(VectorStoreError("upstream down"))
        trace = new_trace()
        with pytest.raises(VectorStoreError):
            upserter.upsert([make_record()], trace=trace)
        events = [s.get("event") for s in trace.stages
                  if s.get("name") == "vector_upserter"]
        assert "error" in events


# ---------------------------------------------------------------------------
# Integration with BatchProcessor output
# ---------------------------------------------------------------------------

class TestBatchProcessorIntegration:
    def test_works_with_records_from_batch_processor(self, upserter, store):
        """Realistic flow: BatchProcessor output → VectorUpserter input."""
        from src.libs.embedding.base_embedding import BaseEmbedding

        class FakeEmb(BaseEmbedding):
            def __init__(self, dim=4):
                self._dim = dim
            @property
            def dimensions(self):
                return self._dim
            def embed(self, texts, **kwargs):
                return [[0.1 * (i + 1)] * self._dim for i, _ in enumerate(texts)]

        from src.ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder
        from src.core.types import Chunk

        bp = BatchProcessor(
            dense_encoder=DenseEncoder(FakeEmb(4)),
            sparse_encoder=SparseEncoder(),
            batch_size=10,
        )
        chunks = [Chunk(id=f"c{i}", text=f"text {i}", metadata={})
                  for i in range(5)]
        records = bp.process(chunks)
        # All records have dense_vector set
        assert all(r.dense_vector is not None for r in records)

        n = upserter.upsert(records)
        assert n == 5
        # All 5 records are in the store with unique ids
        assert len(store.received) == 5
        ids = [r.id for r in store.received]
        assert len(set(ids)) == 5  # all different
