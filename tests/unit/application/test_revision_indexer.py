from datetime import datetime, timezone

import pytest

from src.application.contracts import ChunkRevision, RevisionSource, RevisionStatus
from src.application.services.revision_indexer import (
    RevisionIndexCoordinator,
    RevisionIndexingError,
)
from src.ingestion.storage import RevisionStore


class Participant:
    def __init__(self, name, events, fail=None):
        self.name = name
        self.events = events
        self.fail = fail

    def _event(self, action):
        self.events.append(f"{self.name}:{action}")
        if self.fail == action:
            raise RuntimeError(action)

    def stage(self, revision):
        self._event("stage")
        return f"staged:{self.name}"

    def validate(self, revision, staged):
        self._event("validate")

    def activate(self, revision, staged):
        self._event("activate")
        return f"old:{self.name}"

    def rollback(self, revision, activation):
        self._event("rollback")

    def discard(self, revision, staged):
        self._event("discard")


def _store_with_pending(tmp_path):
    store = RevisionStore(tmp_path / "revisions.sqlite3")
    initial = store.ensure_initial(
        collection="kb", document_id="doc", chunk_id="chunk", text="old",
    )
    pending = ChunkRevision(
        revision_id="r2", collection="kb", document_id="doc", chunk_id="chunk",
        text="new", base_revision_id=initial.revision_id,
        source=RevisionSource.EDIT, reason="fix", actor="user",
        created_at=datetime.now(timezone.utc),
    )
    store.add_pending(pending)
    return store, initial, pending


def test_all_indexes_validate_before_any_activation(tmp_path):
    store, initial, pending = _store_with_pending(tmp_path)
    events = []
    coordinator = RevisionIndexCoordinator(store, [
        Participant("dense", events), Participant("bm25", events),
        Participant("parent", events), Participant("derived", events),
    ])
    result = coordinator.rebuild_and_activate(pending.revision_id)
    assert result.activated is True
    first_activate = next(i for i, event in enumerate(events) if event.endswith(":activate"))
    assert all(event.endswith(":validate") for event in events[4:first_activate])
    assert store.active("kb", "doc", "chunk").revision_id == pending.revision_id
    assert store.status(initial.revision_id) is RevisionStatus.RETIRED


@pytest.mark.parametrize("failed_action", ["stage", "validate", "activate"])
def test_failure_compensates_and_keeps_old_revision_active(tmp_path, failed_action):
    store, initial, pending = _store_with_pending(tmp_path)
    events = []
    coordinator = RevisionIndexCoordinator(store, [
        Participant("dense", events),
        Participant("bm25", events, fail=failed_action),
        Participant("parent", events),
    ])
    with pytest.raises(RevisionIndexingError) as caught:
        coordinator.rebuild_and_activate(pending.revision_id)
    assert caught.value.result.failed_participant == "bm25"
    assert store.active("kb", "doc", "chunk").revision_id == initial.revision_id
    assert store.status(pending.revision_id) is RevisionStatus.INDEX_FAILED
    if failed_action == "activate":
        assert "dense:rollback" in events


def test_failed_revision_can_be_retried(tmp_path):
    store, _, pending = _store_with_pending(tmp_path)
    events = []
    failing = RevisionIndexCoordinator(store, [Participant("dense", events, "validate")])
    with pytest.raises(RevisionIndexingError):
        failing.rebuild_and_activate(pending.revision_id)
    retry = RevisionIndexCoordinator(store, [Participant("dense", events)])
    assert retry.rebuild_and_activate(pending.revision_id).activated is True
