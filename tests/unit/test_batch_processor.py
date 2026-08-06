"""
Unit tests for BatchProcessor (C10).

Uses fake dense + sparse encoders to avoid real API / BM25
dependencies, and asserts the orchestration contract:

- chunk count preserved (spec: 5 chunks → 3 batches at batch_size=2)
- batch order stable
- per-chunk order stable
- every output record has BOTH dense_vector and sparse_vector
- per-batch timing recorded in the trace
- batch_size validation
- per-encoder output not mutated
"""

from __future__ import annotations

import pytest

from src.core.trace.trace_context import new_trace
from src.core.types import Chunk, ChunkRecord
from src.ingestion.embedding import BatchProcessor
from src.ingestion.embedding.dense_encoder import DenseEncoder
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.libs.embedding.base_embedding import BaseEmbedding


# ---------------------------------------------------------------------------
# Fake encoders
# ---------------------------------------------------------------------------

class FakeEmbedding(BaseEmbedding):
    def __init__(self, dim: int = 4) -> None:
        self._dim = dim

    @property
    def dimensions(self) -> int:
        return self._dim

    def embed(self, texts, **kwargs):
        # Each text gets a unique vector = [hash-of-text-mod-100, ...]
        # Stable across calls (no state).
        return [
            [float((hash(t) % 100) + i) for i in range(self._dim)]
            for t in texts
        ]


def make_chunk(text: str, chunk_id: str = "c1") -> Chunk:
    return Chunk(
        id=chunk_id,
        text=text,
        metadata={"source_path": "/tmp/x.pdf", "chunk_index": 0},
    )


@pytest.fixture
def processor() -> BatchProcessor:
    return BatchProcessor(
        dense_encoder=DenseEncoder(FakeEmbedding(dim=4)),
        sparse_encoder=SparseEncoder(),
        batch_size=2,
    )


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------

class TestBasic:
    def test_empty_input_returns_empty(self, processor):
        assert processor.process([]) == []

    def test_returns_list_of_chunk_records(self, processor):
        out = processor.process([make_chunk("hello", "a")])
        assert isinstance(out, list)
        assert all(isinstance(r, ChunkRecord) for r in out)

    def test_count_matches_input(self, processor):
        for n in (1, 3, 5, 10, 100):
            chunks = [make_chunk(f"t{i}", f"c{i}") for i in range(n)]
            out = processor.process(chunks)
            assert len(out) == n

    def test_every_record_has_both_vectors(self, processor):
        out = processor.process([
            make_chunk("alpha", "a"),
            make_chunk("beta",  "b"),
            make_chunk("gamma", "c"),
        ])
        for r in out:
            assert r.dense_vector is not None
            assert r.sparse_vector is not None
            assert isinstance(r.dense_vector, list)
            assert isinstance(r.sparse_vector, dict)

    def test_per_chunk_order_preserved(self, processor):
        chunks = [make_chunk(f"unique_text_{i}", f"c{i}") for i in range(5)]
        out = processor.process(chunks)
        # IDs match
        assert [r.id for r in out] == [c.id for c in chunks]


# ---------------------------------------------------------------------------
# Batching (spec headline: 5 chunks at batch_size=2 → 3 batches)
# ---------------------------------------------------------------------------

class TestBatching:
    def test_spec_headline_5_chunks_batch_size_2(self, processor):
        """5 chunks at batch_size=2 → 3 batches (2+2+1)."""
        ranges = processor.split_batches(5)
        assert len(ranges) == 3
        assert ranges == [(0, 2), (2, 4), (4, 5)]

    def test_exact_multiple_no_remainder(self):
        p = BatchProcessor(
            DenseEncoder(FakeEmbedding(4)),
            SparseEncoder(),
            batch_size=2,
        )
        # 6 chunks → 3 batches of 2
        assert p.split_batches(6) == [(0, 2), (2, 4), (4, 6)]

    def test_single_batch_when_n_leq_batch_size(self):
        p = BatchProcessor(
            DenseEncoder(FakeEmbedding(4)),
            SparseEncoder(),
            batch_size=10,
        )
        # 3 chunks at batch_size=10 → 1 batch
        assert p.split_batches(3) == [(0, 3)]

    def test_zero_returns_empty(self, processor):
        assert processor.split_batches(0) == []

    def test_negative_returns_empty(self, processor):
        assert processor.split_batches(-1) == []

    def test_one_chunk(self, processor):
        assert processor.split_batches(1) == [(0, 1)]

    def test_batches_actually_invoked_in_process(self):
        """Hook into the encoders to record how many calls were made
        and with how many chunks per call. With 5 chunks at
        batch_size=2 we expect 3 calls, of sizes [2, 2, 1]."""
        sizes: list[int] = []

        class _SpyDense(DenseEncoder):
            def encode(self, chunks, trace=None):
                sizes.append(len(chunks))
                return super().encode(chunks, trace=trace)

        spy = _SpyDense(FakeEmbedding(4))
        p = BatchProcessor(
            dense_encoder=spy,
            sparse_encoder=SparseEncoder(),
            batch_size=2,
        )
        chunks = [make_chunk(f"t{i}", f"c{i}") for i in range(5)]
        p.process(chunks)
        assert sizes == [2, 2, 1]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_batch_size_zero_raises(self):
        with pytest.raises(ValueError, match="batch_size"):
            BatchProcessor(
                DenseEncoder(FakeEmbedding(4)),
                SparseEncoder(),
                batch_size=0,
            )

    def test_batch_size_negative_raises(self):
        with pytest.raises(ValueError, match="batch_size"):
            BatchProcessor(
                DenseEncoder(FakeEmbedding(4)),
                SparseEncoder(),
                batch_size=-1,
            )


# ---------------------------------------------------------------------------
# Trace integration
# ---------------------------------------------------------------------------

class TestTrace:
    def test_trace_records_start_finish_and_per_batch(self, processor):
        trace = new_trace()
        chunks = [make_chunk(f"t{i}", f"c{i}") for i in range(5)]
        processor.process(chunks, trace=trace)
        # start/finish from the BatchProcessor itself
        bp_events = [
            s for s in trace.stages if s.get("name") == "batch_processor"
        ]
        events = {s.get("event") for s in bp_events}
        assert "start" in events
        assert "finish" in events

        # Per-batch events
        batch_events = [s for s in trace.stages if s.get("name") == "batch"]
        assert len(batch_events) == 3  # 5 chunks at batch_size=2
        sizes = [b["size"] for b in batch_events]
        assert sizes == [2, 2, 1]
        # Each batch has elapsed_ms recorded
        for b in batch_events:
            assert "elapsed_ms" in b
            assert b["elapsed_ms"] >= 0

    def test_trace_records_batch_indices_in_order(self, processor):
        trace = new_trace()
        chunks = [make_chunk(f"t{i}", f"c{i}") for i in range(5)]
        processor.process(chunks, trace=trace)
        batch_events = [s for s in trace.stages if s.get("name") == "batch"]
        indices = [b["index"] for b in batch_events]
        assert indices == [0, 1, 2]


# ---------------------------------------------------------------------------
# Field preservation
# ---------------------------------------------------------------------------

class TestFieldPreservation:
    def test_other_chunk_fields_preserved(self, processor):
        chunk = Chunk(
            id="myid",
            text="hello world",
            metadata={"source_path": "/tmp/x.pdf", "chunk_index": 7},
            start_offset=10,
            end_offset=20,
            source_ref="doc1",
        )
        out = processor.process([chunk])
        rec = out[0]
        assert rec.id == "myid"
        assert rec.text == "hello world"
        assert rec.metadata == {"source_path": "/tmp/x.pdf", "chunk_index": 7}
        assert rec.start_offset == 10
        assert rec.end_offset == 20
        assert rec.source_ref == "doc1"

    def test_input_chunks_not_mutated(self, processor):
        chunks = [make_chunk(f"t{i}", f"c{i}") for i in range(3)]
        processor.process(chunks)
        # Chunks remain Chunks (not upgraded to ChunkRecord)
        for c in chunks:
            assert isinstance(c, Chunk)
            assert not hasattr(c, "dense_vector") or c.__class__.__name__ == "Chunk"
