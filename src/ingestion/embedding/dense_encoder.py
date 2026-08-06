"""
DenseEncoder (C8) — wraps a ``BaseEmbedding`` and turns a list of
``Chunk`` objects into a list of ``ChunkRecord`` objects with their
``dense_vector`` field populated.

This is the thin adapter between the ingestion pipeline and the
``libs.embedding`` layer. Batching and per-batch tracing are
intentionally left to C10 (``BatchProcessor``) — this class simply
hands the chunk texts to the embedding client in one call. The
underlying client is expected to handle its own batching (e.g.
OpenAI's ``embed`` accepts up to 2048 inputs per call).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.types import Chunk, ChunkRecord
from src.libs.embedding.base_embedding import BaseEmbedding, EmbeddingError

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext


class DenseEncoder:
    """
    Compute dense embedding vectors for each chunk.

    Args:
        embedding: A ``BaseEmbedding`` instance (OpenAI, sentence-
            transformers, …). The encoder is agnostic to the
            provider; it only relies on the public interface.
    """

    name = "dense_encoder"

    def __init__(self, embedding: BaseEmbedding) -> None:
        self.embedding = embedding

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def encode(
        self,
        chunks: list[Chunk],
        trace: "TraceContext | None" = None,
    ) -> list[ChunkRecord]:
        """
        Embed every chunk's text and return ChunkRecords with
        ``dense_vector`` populated.

        Order is preserved: ``chunks[i]`` produces a record whose
        ``dense_vector`` came from the i-th embedding. Empty input
        is a no-op.
        """
        if not chunks:
            return []

        if trace is not None:
            trace.record_stage(
                self.name,
                event="start",
                n_chunks=len(chunks),
                dimensions=self.dimensions,
            )

        texts = [c.text for c in chunks]
        try:
            vectors = self.embedding.embed(texts)
        except EmbeddingError:
            # Surface provider errors untouched so the pipeline can
            # decide whether to abort or fall back. We do NOT
            # silently return empty vectors — that would poison
            # downstream storage with garbage.
            if trace is not None:
                trace.record_stage(self.name, event="error")
            raise

        if len(vectors) != len(chunks):
            raise EmbeddingError(
                f"Embedding returned {len(vectors)} vectors for "
                f"{len(chunks)} chunks — length mismatch"
            )

        records: list[ChunkRecord] = []
        for chunk, vec in zip(chunks, vectors):
            if len(vec) != self.dimensions:
                raise EmbeddingError(
                    f"Embedding returned vector of dim {len(vec)}, "
                    f"expected {self.dimensions}"
                )
            records.append(ChunkRecord.from_chunk(chunk, dense_vector=vec))

        if trace is not None:
            trace.record_stage(self.name, event="finish", n_out=len(records))
        return records

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------
    @property
    def dimensions(self) -> int:
        """Dimensionality of the embedding vectors produced."""
        return self.embedding.dimensions

    def encode_one(self, chunk: Chunk) -> ChunkRecord:
        """Encode a single chunk — convenience for non-batched callers."""
        return self.encode([chunk])[0]
