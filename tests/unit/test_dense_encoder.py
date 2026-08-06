"""
Unit tests for DenseEncoder (C8).

Uses a ``FakeEmbedding`` to avoid real API/HF dependencies.
"""

from __future__ import annotations

import pytest

from src.core.trace.trace_context import new_trace
from src.core.types import Chunk, ChunkRecord
from src.ingestion.embedding import DenseEncoder
from src.libs.embedding.base_embedding import (
    BaseEmbedding,
    EmbeddingError,
)


# ---------------------------------------------------------------------------
# Fake embedding — deterministic, no I/O.
# ---------------------------------------------------------------------------

class FakeEmbedding(BaseEmbedding):
    """
    A fake embedding where the i-th input gets a vector of all ``(i+1)``
    floats, of length ``dim``. Useful for asserting ordering and shape.
    """

    def __init__(self, dim: int = 4) -> None:
        self._dim = dim
        self.call_count = 0
        self.last_inputs: list[str] | None = None

    @property
    def dimensions(self) -> int:
        return self._dim

    def embed(self, texts, **kwargs):
        self.call_count += 1
        self.last_inputs = list(texts)
        out = []
        for i, _ in enumerate(texts):
            out.append([float(i + 1)] * self._dim)
        return out


class DimensionMismatchedEmbedding(BaseEmbedding):
    """Returns vectors of the wrong length — used to test dim check."""

    def __init__(self, expected: int, actual: int) -> None:
        self._expected = expected
        self._actual = actual

    @property
    def dimensions(self) -> int:
        return self._expected

    def embed(self, texts, **kwargs):
        return [[0.0] * self._actual for _ in texts]


class CountingEmbedding(BaseEmbedding):
    """Returns N vectors when N inputs are given — used to test length check."""

    def __init__(self, dim: int, return_count: int) -> None:
        self._dim = dim
        self._return_count = return_count

    @property
    def dimensions(self) -> int:
        return self._dim

    def embed(self, texts, **kwargs):
        return [[0.0] * self._dim] * self._return_count


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def encoder() -> DenseEncoder:
    return DenseEncoder(FakeEmbedding(dim=4))


def make_chunk(text: str, chunk_id: str = "c1") -> Chunk:
    return Chunk(
        id=chunk_id,
        text=text,
        metadata={"source_path": "/tmp/x.pdf", "chunk_index": 0},
    )


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------

class TestBasicEncoding:
    def test_empty_input_returns_empty(self, encoder):
        assert encoder.encode([]) == []

    def test_single_chunk_returns_single_record(self, encoder):
        out = encoder.encode([make_chunk("hi", "a")])
        assert len(out) == 1
        assert isinstance(out[0], ChunkRecord)
        assert out[0].dense_vector == [1.0, 1.0, 1.0, 1.0]

    def test_multiple_chunks_return_same_count(self, encoder):
        chunks = [make_chunk(t, f"c{i}") for i, t in enumerate(
            ["alpha", "beta", "gamma"]
        )]
        out = encoder.encode(chunks)
        assert len(out) == 3
        assert all(isinstance(r, ChunkRecord) for r in out)

    def test_output_count_matches_input_count(self, encoder):
        """The spec's headline acceptance criterion."""
        for n in (1, 5, 17, 100):
            chunks = [make_chunk(f"t{i}", f"c{i}") for i in range(n)]
            out = encoder.encode(chunks)
            assert len(out) == n

    def test_dimensions_consistent_across_vectors(self, encoder):
        chunks = [make_chunk(f"t{i}", f"c{i}") for i in range(5)]
        out = encoder.encode(chunks)
        dims = {len(r.dense_vector) for r in out}
        assert len(dims) == 1
        assert dims.pop() == 4

    def test_dimensions_property_reflects_embedding(self):
        enc = DenseEncoder(FakeEmbedding(dim=768))
        assert enc.dimensions == 768

    def test_chunkrecord_carries_dense_vector(self, encoder):
        out = encoder.encode([make_chunk("x", "a")])
        assert out[0].dense_vector is not None
        assert isinstance(out[0].dense_vector, list)
        assert all(isinstance(v, float) for v in out[0].dense_vector)


# ---------------------------------------------------------------------------
# Order preservation
# ---------------------------------------------------------------------------

class TestOrderPreservation:
    def test_order_matches_input(self, encoder):
        """chunks[i] must produce a record whose vector came from the
        i-th call to the embedder (i.e. its values are (i+1) floats)."""
        chunks = [make_chunk(f"chunk_{i}", f"c{i}") for i in range(5)]
        out = encoder.encode(chunks)
        for i, record in enumerate(out):
            assert record.dense_vector == [float(i + 1)] * 4

    def test_chunk_text_passed_to_embedding_in_order(self):
        emb = FakeEmbedding(dim=4)
        enc = DenseEncoder(emb)
        chunks = [make_chunk("alpha"), make_chunk("beta"), make_chunk("gamma")]
        enc.encode(chunks)
        assert emb.last_inputs == ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------
# Field preservation (id, text, metadata, source_ref)
# ---------------------------------------------------------------------------

class TestFieldPreservation:
    def test_other_chunk_fields_preserved(self, encoder):
        chunk = Chunk(
            id="myid",
            text="hello",
            metadata={"source_path": "/tmp/x.pdf", "chunk_index": 7},
            start_offset=10,
            end_offset=20,
            source_ref="doc1",
        )
        out = encoder.encode([chunk])
        rec = out[0]
        assert rec.id == "myid"
        assert rec.text == "hello"
        assert rec.metadata == {"source_path": "/tmp/x.pdf", "chunk_index": 7}
        assert rec.start_offset == 10
        assert rec.end_offset == 20
        assert rec.source_ref == "doc1"

    def test_sparse_vector_left_none(self, encoder):
        """DenseEncoder only fills dense_vector; sparse stays None."""
        out = encoder.encode([make_chunk("x", "a")])
        assert out[0].sparse_vector is None

    def test_input_chunk_not_mutated(self, encoder):
        chunk = make_chunk("x", "a")
        encoder.encode([chunk])
        # Input Chunk has no dense_vector (it's a Chunk, not ChunkRecord)
        # Verify the input wasn't replaced
        assert isinstance(chunk, Chunk)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_embedding_error_propagates(self):
        class BoomEmbedding(BaseEmbedding):
            @property
            def dimensions(self):
                return 4

            def embed(self, texts, **kwargs):
                raise EmbeddingError("API down")

        enc = DenseEncoder(BoomEmbedding())
        with pytest.raises(EmbeddingError, match="API down"):
            enc.encode([make_chunk("x")])

    def test_length_mismatch_raises(self):
        """If the embedding returns a different number of vectors than
        the number of inputs, raise — this would silently corrupt
        chunk→vector alignment downstream."""
        enc = DenseEncoder(CountingEmbedding(dim=4, return_count=2))
        with pytest.raises(EmbeddingError, match="length mismatch"):
            enc.encode([make_chunk("a"), make_chunk("b"), make_chunk("c")])

    def test_wrong_dim_per_vector_raises(self):
        enc = DenseEncoder(DimensionMismatchedEmbedding(expected=4, actual=5))
        with pytest.raises(EmbeddingError, match="dim"):
            enc.encode([make_chunk("x")])


# ---------------------------------------------------------------------------
# Trace integration
# ---------------------------------------------------------------------------

class TestTrace:
    def test_trace_records_start_and_finish(self, encoder):
        trace = new_trace()
        encoder.encode([make_chunk("a"), make_chunk("b")], trace=trace)
        events = [s.get("event") for s in trace.stages if s.get("name") == "dense_encoder"]
        assert "start" in events
        assert "finish" in events

    def test_trace_records_dimensions(self, encoder):
        trace = new_trace()
        encoder.encode([make_chunk("a")], trace=trace)
        start = next(s for s in trace.stages
                     if s.get("name") == "dense_encoder"
                     and s.get("event") == "start")
        assert start["dimensions"] == 4
        assert start["n_chunks"] == 1

    def test_trace_records_error_on_failure(self):
        class Boom(BaseEmbedding):
            @property
            def dimensions(self):
                return 4
            def embed(self, texts, **kwargs):
                raise EmbeddingError("nope")

        trace = new_trace()
        with pytest.raises(EmbeddingError):
            DenseEncoder(Boom()).encode([make_chunk("a")], trace=trace)
        assert any(
            s.get("event") == "error"
            for s in trace.stages
            if s.get("name") == "dense_encoder"
        )


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

class TestConvenience:
    def test_encode_one(self, encoder):
        rec = encoder.encode_one(make_chunk("only", "z"))
        assert isinstance(rec, ChunkRecord)
        assert rec.id == "z"
        assert rec.dense_vector == [1.0, 1.0, 1.0, 1.0]
