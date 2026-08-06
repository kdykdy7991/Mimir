"""
BatchProcessor (C10) — orchestrates dense + sparse encoding in chunks.

Splits a list of ``Chunk`` objects into fixed-size batches, runs the
``DenseEncoder`` and ``SparseEncoder`` on each batch, then fuses the
per-encoder outputs into ``ChunkRecord`` objects that carry BOTH
``dense_vector`` and ``sparse_vector``. Per-batch wall-clock time is
recorded in the trace for observability.

Why batch
---------
* Some embedding providers (OpenAI, Cohere, vLLM) charge per request
  but accept many inputs per request — batching amortizes overhead.
* A single huge call can OOM; bounded batches keep memory stable.
* Trace granularity per batch makes slowdowns easy to spot.

The batch_size is the number of ``Chunk``s per batch, not the
number of tokens — the right unit for "how many API calls" or
"how much GPU memory per step".
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Callable

from src.core.types import Chunk, ChunkRecord
from src.ingestion.embedding.dense_encoder import DenseEncoder
from src.ingestion.embedding.sparse_encoder import SparseEncoder

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext


class BatchProcessor:
    """
    Run dense + sparse encoding in batches, fuse per-chunk results.

    Args:
        dense_encoder: The :class:`DenseEncoder` (or any object with
            a compatible ``encode(chunks) -> list[ChunkRecord]``).
        sparse_encoder: The :class:`SparseEncoder` (same contract).
        batch_size: Number of chunks per batch. Must be >= 1.
            Default 32 — a good middle ground for OpenAI's 2048-
            input limit and a single GPU's memory budget.
    """

    name = "batch_processor"

    def __init__(
        self,
        dense_encoder: DenseEncoder,
        sparse_encoder: SparseEncoder,
        batch_size: int = 32,
    ) -> None:
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        self.dense = dense_encoder
        self.sparse = sparse_encoder
        self.batch_size = batch_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def process(
        self,
        chunks: list[Chunk],
        trace: "TraceContext | None" = None,
        on_progress: "Callable[[str, int, int], None] | None" = None,
    ) -> list[ChunkRecord]:
        """
        Encode every chunk with both encoders, in batches, and return
        a flat list of ``ChunkRecord`` with both vectors populated.

        Order is preserved: ``result[i]`` corresponds to ``chunks[i]``.

        Args:
            chunks: Input chunks to encode.
            trace: Optional ``TraceContext`` (F4 — records per-batch
                timing on ``self.name``).
            on_progress: Optional callback ``(stage_name, current,
                total) -> None`` invoked once per batch with
                ``stage_name="embed"``. Used by the Dashboard
                to render a per-batch progress bar. ``None``
                disables progress reporting without affecting the
                encode pipeline.
        """
        if not chunks:
            return []

        if trace is not None:
            trace.record_stage(
                self.name,
                event="start",
                n_chunks=len(chunks),
                batch_size=self.batch_size,
            )

        n_batches = self._batch_count(len(chunks))
        all_records: list[ChunkRecord] = []
        for batch_idx, start in enumerate(range(0, len(chunks), self.batch_size)):
            batch = chunks[start:start + self.batch_size]
            batch_records = self._process_one_batch(
                batch, batch_idx, trace
            )
            all_records.extend(batch_records)
            # Per-batch progress (F5). current is 1-indexed so the
            # UI can show "1/3" instead of "0/3".
            if on_progress is not None:
                on_progress("embed", batch_idx + 1, n_batches)

        if trace is not None:
            trace.record_stage(
                self.name, event="finish",
                n_out=len(all_records),
                n_batches=n_batches,
            )
        return all_records

    # ------------------------------------------------------------------
    # Batch helpers
    # ------------------------------------------------------------------
    def _process_one_batch(
        self,
        batch: list[Chunk],
        batch_idx: int,
        trace: "TraceContext | None",
    ) -> list[ChunkRecord]:
        t0 = time.perf_counter()
        dense_records = self.dense.encode(batch)
        sparse_records = self.sparse.encode(batch)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        if len(dense_records) != len(batch):
            raise RuntimeError(
                f"dense encoder returned {len(dense_records)} records "
                f"for batch of {len(batch)}"
            )
        if len(sparse_records) != len(batch):
            raise RuntimeError(
                f"sparse encoder returned {len(sparse_records)} records "
                f"for batch of {len(batch)}"
            )

        merged = [
            replace(d, sparse_vector=s.sparse_vector)
            for d, s in zip(dense_records, sparse_records)
        ]

        if trace is not None:
            trace.record_stage(
                "batch",
                index=batch_idx,
                size=len(batch),
                elapsed_ms=elapsed_ms,
            )
        return merged

    def _batch_count(self, n: int) -> int:
        """How many batches would ``n`` chunks split into."""
        return (n + self.batch_size - 1) // self.batch_size

    def split_batches(self, n: int) -> list[tuple[int, int]]:
        """
        Pure helper — return the ``(start, end)`` ranges that
        ``process`` would iterate over. Useful for tests + callers
        that want to know the partition without running it.
        """
        if n <= 0:
            return []
        out: list[tuple[int, int]] = []
        for start in range(0, n, self.batch_size):
            out.append((start, min(start + self.batch_size, n)))
        return out
