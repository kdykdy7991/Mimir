"""
VectorUpserter (C12) — deterministic-id upsert into a vector store.

Converts a list of ``ChunkRecord`` (output of :class:`DenseEncoder` /
:class:`BatchProcessor`) into ``VectorRecord`` objects with
*content-derived stable IDs* and pushes them into a
:class:`BaseVectorStore`. Stable IDs are the key to idempotency:

  id = SHA256( source_path | chunk_index | sha256(text)[:8] )  (truncated)

Same ``source_path`` + same ``chunk_index`` + same text → same id.
A change to any of those three components → new id. So re-ingesting
a modified chunk creates a new record, while a re-ingest of an
unchanged chunk overwrites the existing record (idempotent at the
VectorStore layer, which is expected to handle "same id, new
vector" by replacing).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from src.core.types import ChunkRecord
from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    VectorRecord,
    VectorStoreError,
)

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext


# Length of the SHA-256 digest we keep in the final id. 16 hex chars
# = 64 bits of collision space — plenty for a single corpus, and
# short enough to be human-readable in logs.
_ID_LEN = 16


class VectorUpserter:
    """
    Idempotent upsert wrapper around a :class:`BaseVectorStore`.

    Args:
        store: The vector store implementation (Chroma, Qdrant, …).
            Any object with a compatible ``upsert(records) -> int``
            method works.
    """

    name = "vector_upserter"

    def __init__(self, store: BaseVectorStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def upsert(
        self,
        records: list[ChunkRecord],
        trace: "TraceContext | None" = None,
    ) -> int:
        """
        Upsert every record into the configured vector store.

        Returns the number of records successfully upserted (as
        reported by the store). Order of records is preserved in
        the call to the store.

        Raises:
            ValueError: If any record has no ``dense_vector`` set.
            VectorStoreError: If the store's upsert fails.
        """
        if not records:
            return 0

        if trace is not None:
            trace.record_stage(
                self.name, event="start", n_records=len(records),
            )

        # Validate upfront: a chunk without a dense vector would be
        # useless to the store, so fail fast and clearly.
        for i, rec in enumerate(records):
            if rec.dense_vector is None:
                raise ValueError(
                    f"record at index {i} (id={rec.id!r}) has no "
                    f"dense_vector — encode it before upserting"
                )

        vector_records = [self._build(rec) for rec in records]
        try:
            n = self.store.upsert(vector_records)
        except VectorStoreError:
            if trace is not None:
                trace.record_stage(self.name, event="error")
            raise

        if trace is not None:
            trace.record_stage(
                self.name, event="finish", n_out=n,
            )
        return n

    # ------------------------------------------------------------------
    # ID generation (exposed for tests + D2 dense retrieval that needs
    # the same id to look records up).
    # ------------------------------------------------------------------
    @staticmethod
    def make_id(record: ChunkRecord) -> str:
        """
        Deterministic id for a chunk record.

        Formula: ``sha256(source_path | chunk_index | content_hash[:8])``
        truncated to 16 hex chars.
        """
        source_path = record.metadata.get("source_path", "")
        # ``chunk_index`` is written into metadata by the chunker
        # (C4). Default to 0 if missing so the id is still stable
        # for records produced without the chunker (e.g. ad-hoc
        # unit-test inputs).
        chunk_index = record.metadata.get("chunk_index", 0)
        content_hash = hashlib.sha256(
            (record.text or "").encode("utf-8")
        ).hexdigest()[:8]
        key = f"{source_path}|{chunk_index}|{content_hash}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:_ID_LEN]

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------
    def _build(self, record: ChunkRecord) -> VectorRecord:
        """Convert a ChunkRecord into the store's VectorRecord shape.

        The vector-store id is the original ``ChunkRecord.id`` so that
        dense and sparse retrieval agree on the same chunk identifier.
        ``make_id`` is kept for backward compatibility but is no longer
        used for new writes.
        """
        meta = dict(record.metadata)
        # Surface the original chunk id too, so the dashboard / retriever
        # can join back to the chunk even if a store rewrites ids.
        meta.setdefault("chunk_id", record.id)
        return VectorRecord(
            id=record.id,
            vector=list(record.dense_vector or []),
            text=record.text or "",
            metadata=meta,
        )
