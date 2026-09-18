from datetime import datetime, timezone

from src.application.contracts import ChunkRevision, RevisionSource
from src.application.services.revision_index_participants import (
    BM25RevisionParticipant,
    DenseRevisionParticipant,
    DerivedRevisionParticipant,
    ParentChildRevisionParticipant,
)
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage import BM25Indexer, ParentChunkStore, RevisionStore
from src.ingestion.chunking import ParentChunkBuilder
from src.core.types import Chunk, ChunkRecord
from src.application.services.derived_content_service import DerivedContentService
from src.ingestion.storage import DerivedContentStore


def _revisions(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    initial = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="chunk",
        text="old words", metadata={"source_path": "a.md"},
    )
    revision = ChunkRevision(
        revision_id="r2", collection="kb", document_id="doc", chunk_id="chunk",
        text="new terms", metadata={"source_path": "a.md"},
        base_revision_id=initial.revision_id, source=RevisionSource.EDIT,
        reason="fix", actor="user", created_at=datetime.now(timezone.utc),
    )
    store.add_pending(revision)
    return store, revision


class Embedding:
    def embed(self, texts):
        return [[float(len(text)), 1.0] for text in texts]


class VectorStore:
    def __init__(self):
        self.records = {}

    def upsert(self, records):
        for record in records:
            self.records[record.id] = record
        return len(records)


def test_dense_participant_can_activate_and_restore_base(tmp_path):
    store, revision = _revisions(tmp_path)
    vectors = VectorStore()
    participant = DenseRevisionParticipant(store, vectors, Embedding())
    staged = participant.stage(revision)
    participant.validate(revision, staged)
    old = participant.activate(revision, staged)
    assert vectors.records["chunk"].text == "new terms"
    assert vectors.records["chunk"].metadata["revision_id"] == "r2"
    participant.rollback(revision, old)
    assert vectors.records["chunk"].text == "old words"


def test_bm25_participant_stages_saves_and_rolls_back(tmp_path):
    _, revision = _revisions(tmp_path)
    indexer = BM25Indexer(
        persist_dir=str(tmp_path / "bm25"), sparse_encoder=SparseEncoder(),
    )
    old = indexer.build([ChunkRecord(id="chunk", text="old words")])
    indexer.save(old, name="kb")
    participant = BM25RevisionParticipant(indexer)
    staged = participant.stage(revision)
    participant.validate(revision, staged)
    prior = participant.activate(revision, staged)
    assert indexer.query(indexer.load("kb"), "terms")[0].chunk_id == "chunk"
    participant.rollback(revision, prior)
    assert indexer.query(indexer.load("kb"), "old")[0].chunk_id == "chunk"
    participant.discard(revision, staged)


def test_parent_participant_rebuilds_document_and_restores_previous(tmp_path):
    _, revision = _revisions(tmp_path)
    store = ParentChunkStore(tmp_path / "parents.sqlite3")
    chunks = [
        Chunk(
            id="chunk", text="old words",
            metadata={"document_id": "doc", "document_version": "v1", "chunk_index": 0},
            source_ref="doc",
        ),
        Chunk(
            id="other", text="other text",
            metadata={"document_id": "doc", "document_version": "v1", "chunk_index": 1},
            source_ref="doc",
        ),
    ]
    builder = ParentChunkBuilder()
    old = builder.build(chunks)
    store.stage(collection="kb", document_id="doc", version="v1",
                children=old.children, parents=old.parents)
    store.activate(collection="kb", document_id="doc", version="v1")
    participant = ParentChildRevisionParticipant(store, builder, lambda c, d: chunks)
    staged = participant.stage(revision)
    participant.validate(revision, staged)
    previous = participant.activate(revision, staged)
    assert store.active_version("kb", "doc") == "r2"
    participant.rollback(revision, previous)
    assert store.active_version("kb", "doc") == "v1"
    participant.discard(revision, staged)


def test_derived_participant_is_staged_and_compensatable(tmp_path):
    _, revision = _revisions(tmp_path)
    store = DerivedContentStore(tmp_path / "derived.sqlite3")
    service = DerivedContentService(
        store, lambda text: {"summary": "S", "questions": ["Q?"]},
        model="m", prompt_version="p", enabled=True,
    )
    participant = DerivedRevisionParticipant(service)
    staged = participant.stage(revision)
    assert store.list("r2") == []
    participant.validate(revision, staged)
    previous = participant.activate(revision, staged)
    assert len(store.list("r2")) == 2
    participant.rollback(revision, previous)
    assert store.list("r2") == []
