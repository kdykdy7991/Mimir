from datetime import datetime, timezone

import pytest

from src.application.contracts import ChunkRevision, RevisionSource, RevisionStatus
from src.ingestion.storage.revision_store import (
    RevisionConflictError,
    RevisionStore,
    RevisionStoreError,
)


def _pending(base: str, revision_id: str = "r2", text: str = "edited") -> ChunkRevision:
    return ChunkRevision(
        revision_id=revision_id, collection="kb", document_id="doc", chunk_id="chunk",
        text=text, base_revision_id=base, source=RevisionSource.EDIT,
        reason="correct typo", actor="user:7", created_at=datetime.now(timezone.utc),
    )


def test_legacy_initial_is_lazy_idempotent_and_active(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    first = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="chunk", text="original",
    )
    second = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="chunk", text="different ignored",
    )
    assert first.revision_id == second.revision_id
    assert store.active("kb", "doc", "chunk").text == "original"
    assert store.status(first.revision_id) is RevisionStatus.ACTIVE


def test_pending_activation_is_atomic_and_retires_previous(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    initial = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="chunk", text="original",
    )
    pending = _pending(initial.revision_id)
    store.add_pending(pending)
    assert store.active("kb", "doc", "chunk").revision_id == initial.revision_id
    store.activate(pending.revision_id)
    assert store.active("kb", "doc", "chunk").revision_id == pending.revision_id
    assert store.status(initial.revision_id) is RevisionStatus.RETIRED
    assert [state for _, state in store.history("kb", "doc", "chunk")] == [
        RevisionStatus.RETIRED, RevisionStatus.ACTIVE,
    ]


def test_stale_base_is_rejected_before_and_during_activation(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    initial = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="chunk", text="original",
    )
    with pytest.raises(RevisionConflictError):
        store.add_pending(_pending("stale"))
    first = _pending(initial.revision_id, "r2")
    second = _pending(initial.revision_id, "r3")
    store.add_pending(first)
    store.add_pending(second)
    store.activate(first.revision_id)
    with pytest.raises(RevisionConflictError):
        store.activate(second.revision_id)


def test_index_failure_keeps_old_active_and_can_retry(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    initial = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="chunk", text="original",
    )
    pending = _pending(initial.revision_id)
    store.add_pending(pending)
    store.mark_index_failed(pending.revision_id)
    assert store.active("kb", "doc", "chunk").revision_id == initial.revision_id
    assert store.status(pending.revision_id) is RevisionStatus.INDEX_FAILED
    store.activate(pending.revision_id)
    assert store.active("kb", "doc", "chunk").revision_id == pending.revision_id


def test_revision_id_cannot_be_reused_for_different_content(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    initial = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="chunk", text="original",
    )
    store.add_pending(_pending(initial.revision_id, "r2", "first"))
    with pytest.raises(RevisionStoreError, match="different content"):
        store.add_pending(_pending(initial.revision_id, "r2", "second"))


def test_rollback_target_must_belong_to_same_chunk(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    initial = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="chunk", text="original",
    )
    foreign = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="other", text="foreign",
    )
    rollback = ChunkRevision(
        revision_id="rollback", collection="kb", document_id="doc",
        chunk_id="chunk", text="foreign", base_revision_id=initial.revision_id,
        source=RevisionSource.ROLLBACK,
        rollback_target_revision_id=foreign.revision_id,
        reason="rollback", actor="user:7",
    )
    with pytest.raises(RevisionStoreError, match="different chunk"):
        store.add_pending(rollback)
