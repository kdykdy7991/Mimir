"""Production Dense and BM25 participants for revision re-indexing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.application.contracts import ChunkRevision
from src.application.contracts import DerivedArtifact
from src.core.types import ChunkRecord
from src.core.types import Chunk
from src.ingestion.storage import ParentChunkStore, RevisionStore
from src.ingestion.storage.bm25_locks import bm25_write_lock
from src.libs.vector_store.base_vector_store import VectorRecord


@dataclass
class _DenseStage:
    new: VectorRecord
    old: VectorRecord


class DenseRevisionParticipant:
    name = "dense"

    def __init__(self, revision_store: RevisionStore, vector_store: Any, embedding: Any) -> None:
        self.revision_store = revision_store
        self.vector_store = vector_store
        self.embedding = embedding

    def stage(self, revision: ChunkRevision) -> _DenseStage:
        if not revision.base_revision_id:
            raise ValueError("revision requires a base for dense rebuild")
        base = self.revision_store.get(revision.base_revision_id)
        vectors = self.embedding.embed([revision.text, base.text])
        if len(vectors) != 2:
            raise ValueError("embedding provider returned an unexpected batch size")
        new_meta = dict(revision.metadata)
        new_meta.update({
            "chunk_id": revision.chunk_id,
            "chunk_version": revision.checksum_sha256,
            "document_version": revision.revision_id,
            "revision_id": revision.revision_id,
        })
        old_meta = dict(base.metadata)
        old_meta.setdefault("chunk_id", base.chunk_id)
        return _DenseStage(
            new=VectorRecord(revision.chunk_id, list(vectors[0]), revision.text, new_meta),
            old=VectorRecord(base.chunk_id, list(vectors[1]), base.text, old_meta),
        )

    def validate(self, revision: ChunkRevision, staged: _DenseStage) -> None:
        if not staged.new.vector or len(staged.new.vector) != len(staged.old.vector):
            raise ValueError("dense vectors must be non-empty with stable dimensions")

    def activate(self, revision: ChunkRevision, staged: _DenseStage) -> VectorRecord:
        if self.vector_store.upsert([staged.new]) != 1:
            raise RuntimeError("dense index did not upsert exactly one chunk")
        return staged.old

    def rollback(self, revision: ChunkRevision, activation: VectorRecord) -> None:
        if self.vector_store.upsert([activation]) != 1:
            raise RuntimeError("dense rollback did not restore exactly one chunk")

    def discard(self, revision: ChunkRevision, staged: _DenseStage) -> None:
        return None


@dataclass
class _BM25Stage:
    new_index: Any
    old_index: Any
    lock: Any


class BM25RevisionParticipant:
    name = "bm25"

    def __init__(self, indexer: Any) -> None:
        self.indexer = indexer

    def stage(self, revision: ChunkRevision) -> _BM25Stage:
        import copy

        lock = bm25_write_lock(revision.collection)
        lock.acquire()
        try:
            old = self.indexer.load(name=revision.collection)
            new = copy.deepcopy(old)
            record = ChunkRecord(
                id=revision.chunk_id, text=revision.text,
                metadata={
                    **dict(revision.metadata),
                    "chunk_version": revision.checksum_sha256,
                    "document_version": revision.revision_id,
                    "revision_id": revision.revision_id,
                },
            )
            self.indexer.add(new, [record])
            return _BM25Stage(new_index=new, old_index=old, lock=lock)
        except Exception:
            lock.release()
            raise

    def validate(self, revision: ChunkRevision, staged: _BM25Stage) -> None:
        if staged.new_index.n_docs < 1:
            raise ValueError("BM25 staged index is empty")

    def activate(self, revision: ChunkRevision, staged: _BM25Stage) -> Any:
        self.indexer.save(staged.new_index, name=revision.collection)
        return staged.old_index

    def rollback(self, revision: ChunkRevision, activation: Any) -> None:
        self.indexer.save(activation, name=revision.collection)

    def discard(self, revision: ChunkRevision, staged: _BM25Stage) -> None:
        staged.lock.release()


@dataclass
class _ParentStage:
    version: str
    previous_version: str | None


class ParentChildRevisionParticipant:
    name = "parent_child"

    def __init__(
        self, store: ParentChunkStore, builder: Any,
        document_chunks_reader: Callable[[str, str], list[Chunk]],
    ) -> None:
        self.store = store
        self.builder = builder
        self.document_chunks_reader = document_chunks_reader

    def stage(self, revision: ChunkRevision) -> _ParentStage:
        chunks = self.document_chunks_reader(
            revision.collection, revision.document_id,
        )
        found = False
        versioned: list[Chunk] = []
        for chunk in chunks:
            metadata = dict(chunk.metadata)
            metadata["document_version"] = revision.revision_id
            if chunk.id == revision.chunk_id:
                found = True
                metadata.update(dict(revision.metadata))
                metadata["chunk_version"] = revision.checksum_sha256
                chunk = Chunk(
                    id=chunk.id, text=revision.text, metadata=metadata,
                    start_offset=chunk.start_offset, end_offset=chunk.end_offset,
                    source_ref=chunk.source_ref,
                )
            else:
                chunk = Chunk(
                    id=chunk.id, text=chunk.text, metadata=metadata,
                    start_offset=chunk.start_offset, end_offset=chunk.end_offset,
                    source_ref=chunk.source_ref,
                )
            versioned.append(chunk)
        if not found:
            raise ValueError("edited chunk is absent from its document")
        built = self.builder.build(versioned)
        previous = self.store.active_version(
            revision.collection, revision.document_id,
        )
        self.store.stage(
            collection=revision.collection, document_id=revision.document_id,
            version=revision.revision_id, children=built.children,
            parents=built.parents,
        )
        return _ParentStage(revision.revision_id, previous)

    def validate(self, revision: ChunkRevision, staged: _ParentStage) -> None:
        if staged.version != revision.revision_id:
            raise ValueError("parent-child staged version mismatch")

    def activate(self, revision: ChunkRevision, staged: _ParentStage) -> str | None:
        self.store.activate(
            collection=revision.collection, document_id=revision.document_id,
            version=staged.version,
        )
        return staged.previous_version

    def rollback(self, revision: ChunkRevision, activation: str | None) -> None:
        self.store.restore_active(
            revision.collection, revision.document_id, activation,
        )

    def discard(self, revision: ChunkRevision, staged: _ParentStage) -> None:
        self.store.delete_staging(
            revision.collection, revision.document_id, staged.version,
        )


class DerivedRevisionParticipant:
    name = "derived"

    def __init__(self, service: Any) -> None:
        self.service = service

    def stage(self, revision: ChunkRevision) -> tuple[DerivedArtifact, ...]:
        return self.service.generate(revision)

    def validate(
        self, revision: ChunkRevision, staged: tuple[DerivedArtifact, ...],
    ) -> None:
        if any(row.revision_id != revision.revision_id for row in staged):
            raise ValueError("derived artifact revision mismatch")

    def activate(
        self, revision: ChunkRevision, staged: tuple[DerivedArtifact, ...],
    ) -> tuple[DerivedArtifact, ...]:
        previous = tuple(self.service.store.list(revision.revision_id))
        self.service.store.replace_revision(revision.revision_id, list(staged))
        return previous

    def rollback(
        self, revision: ChunkRevision, activation: tuple[DerivedArtifact, ...],
    ) -> None:
        self.service.store.replace_revision(revision.revision_id, list(activation))

    def discard(
        self, revision: ChunkRevision, staged: tuple[DerivedArtifact, ...],
    ) -> None:
        return None


__all__ = [
    "BM25RevisionParticipant", "DenseRevisionParticipant",
    "DerivedRevisionParticipant", "ParentChildRevisionParticipant",
]
