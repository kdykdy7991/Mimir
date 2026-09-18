from datetime import datetime, timezone

import pytest

from src.application.contracts import ChunkRevision, RevisionSource
from src.application.services.revision_service import (
    AuthorizedChunkSnapshot,
    RevisionNotFoundOrAccessibleError,
    RevisionService,
)
from src.ingestion.storage.revision_store import RevisionStore
from src.ingestion.storage.revision_store import RevisionConflictError


def _reader(document_id, chunk_id, principal):
    if principal != "allowed" or document_id != "doc" or chunk_id != "chunk":
        raise PermissionError
    return AuthorizedChunkSnapshot(
        collection="kb", document_id=document_id, chunk_id=chunk_id,
        text="line one\nline two\n", metadata={"heading": "Intro"},
    )


def _edited(store, base, *, revision_id="r2", text="line one\nchanged\n"):
    revision = ChunkRevision(
        revision_id=revision_id, collection="kb", document_id="doc",
        chunk_id="chunk", text=text, base_revision_id=base,
        source=RevisionSource.EDIT, reason="fix", actor="user:1",
        created_at=datetime.now(timezone.utc),
    )
    store.add_pending(revision)
    store.activate(revision_id)
    return revision


def test_history_lazily_creates_one_initial_revision(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    service = RevisionService(store, _reader)
    first = service.list_history("doc", "chunk", "allowed")
    second = service.list_history("doc", "chunk", "allowed")
    assert len(first) == len(second) == 1
    assert first[0].source is RevisionSource.INITIAL
    assert first[0].is_current is True


def test_history_detail_and_diff_preserve_old_content(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    service = RevisionService(store, _reader)
    initial_id = service.list_history("doc", "chunk", "allowed")[0].revision_id
    _edited(store, initial_id)
    history = service.list_history("doc", "chunk", "allowed")
    assert [item.is_current for item in history] == [False, True]
    old, _, is_current = service.get_revision(
        "doc", "chunk", initial_id, "allowed",
    )
    assert old.text == "line one\nline two\n"
    assert is_current is False
    diff = service.diff("doc", "chunk", initial_id, "r2", "allowed")
    assert "-line two" in diff.unified_diff
    assert "+changed" in diff.unified_diff
    assert diff.truncated is False


def test_diff_enforces_input_and_output_budgets(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    service = RevisionService(
        store, _reader, max_diff_input_chars=8, max_diff_output_chars=12,
    )
    initial_id = service.list_history("doc", "chunk", "allowed")[0].revision_id
    _edited(store, initial_id, text="completely different text\n")
    result = service.diff("doc", "chunk", initial_id, "r2", "allowed")
    assert result.truncated is True
    assert len(result.unified_diff) <= 12


def test_access_and_cross_chunk_revision_are_same_shape(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    service = RevisionService(store, _reader)
    with pytest.raises(RevisionNotFoundOrAccessibleError, match="not accessible"):
        service.list_history("doc", "chunk", "denied")
    service.list_history("doc", "chunk", "allowed")
    foreign = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="other", text="secret",
    )
    with pytest.raises(RevisionNotFoundOrAccessibleError, match="not accessible"):
        service.get_revision("doc", "chunk", foreign.revision_id, "allowed")


def test_create_edit_is_pending_and_whitelists_metadata(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    service = RevisionService(store, _reader)
    base = service.list_history("doc", "chunk", "allowed")[0].revision_id
    revision = service.create_edit(
        "doc", "chunk", "allowed", base_revision_id=base,
        text="corrected", metadata_patch={"section": "Fixed"},
        actor="user:1", reason="factual correction",
    )
    assert revision.base_revision_id == base
    assert revision.metadata["section"] == "Fixed"
    assert store.status(revision.revision_id).value == "pending_index"
    assert store.active("kb", "doc", "chunk").revision_id == base
    with pytest.raises(ValueError, match="not editable"):
        service.create_edit(
            "doc", "chunk", "allowed", base_revision_id=base,
            metadata_patch={"document_id": "other"},
            actor="user:1", reason="bad",
        )


def test_create_edit_rejects_noop_and_stale_base(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    service = RevisionService(store, _reader)
    base = service.list_history("doc", "chunk", "allowed")[0].revision_id
    with pytest.raises(ValueError, match="must change"):
        service.create_edit(
            "doc", "chunk", "allowed", base_revision_id=base,
            actor="user:1", reason="noop",
        )
    with pytest.raises(RevisionConflictError):
        service.create_edit(
            "doc", "chunk", "allowed", base_revision_id="stale",
            text="changed", actor="user:1", reason="stale",
        )


def test_rollback_creates_new_pending_revision_without_reactivating_history(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    service = RevisionService(store, _reader)
    initial = service.list_history("doc", "chunk", "allowed")[0].revision_id
    edited = _edited(store, initial)
    rollback = service.create_rollback(
        "doc", "chunk", "allowed", base_revision_id=edited.revision_id,
        target_revision_id=initial, actor="user:1", reason="undo",
    )
    assert rollback.revision_id not in {initial, edited.revision_id}
    assert rollback.rollback_target_revision_id == initial
    assert rollback.text == "line one\nline two\n"
    assert store.active("kb", "doc", "chunk").revision_id == edited.revision_id
