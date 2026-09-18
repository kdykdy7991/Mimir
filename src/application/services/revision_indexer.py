"""Coordinated multi-index rebuild and revision activation (Task 07.4)."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from src.application.contracts import ChunkRevision, RevisionStatus
from src.ingestion.storage import RevisionStore


class RevisionIndexParticipant(Protocol):
    """One transactional participant (Dense, BM25, parent or derived).

    ``stage`` must either return a discardable handle or clean its own partial
    writes before raising. ``activate`` must return enough prior state for
    ``rollback`` to restore the pre-call active view.
    """

    name: str

    def stage(self, revision: ChunkRevision) -> Any: ...
    def validate(self, revision: ChunkRevision, staged: Any) -> None: ...
    def activate(self, revision: ChunkRevision, staged: Any) -> Any: ...
    def rollback(self, revision: ChunkRevision, activation: Any) -> None: ...
    def discard(self, revision: ChunkRevision, staged: Any) -> None: ...


@dataclass(frozen=True)
class RevisionIndexResult:
    revision_id: str
    activated: bool
    participants: tuple[str, ...]
    failed_participant: str | None = None
    error_type: str | None = None


class RevisionIndexingError(RuntimeError):
    def __init__(self, result: RevisionIndexResult) -> None:
        self.result = result
        super().__init__(
            f"revision indexing failed at {result.failed_participant}: "
            f"{result.error_type}",
        )


class RevisionIndexCoordinator:
    """Stages every index before switching any active pointer.

    Participants must implement compensating rollback for activation. Revision
    activation is deliberately last, so read paths keep the old revision until
    every index has validated and activated successfully.
    """

    def __init__(
        self, store: RevisionStore,
        participants: Sequence[RevisionIndexParticipant],
    ) -> None:
        names = [participant.name for participant in participants]
        if not names or any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("index participants require unique non-empty names")
        self.store = store
        self.participants = tuple(participants)
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str, str], threading.Lock] = {}

    def rebuild_and_activate(self, revision_id: str) -> RevisionIndexResult:
        revision = self.store.get(revision_id)
        key = (revision.collection, revision.document_id, revision.chunk_id)
        with self._lock_for(key):
            status = self.store.status(revision_id)
            if status not in {RevisionStatus.PENDING_INDEX, RevisionStatus.INDEX_FAILED}:
                raise ValueError("revision is not pending indexing")
            staged: list[tuple[RevisionIndexParticipant, Any]] = []
            activated: list[tuple[RevisionIndexParticipant, Any]] = []
            current: RevisionIndexParticipant | None = None
            try:
                for participant in self.participants:
                    current = participant
                    payload = participant.stage(revision)
                    staged.append((participant, payload))
                for participant, payload in staged:
                    current = participant
                    participant.validate(revision, payload)
                for participant, payload in staged:
                    current = participant
                    activation = participant.activate(revision, payload)
                    activated.append((participant, activation))
                current = None
                self.store.activate(revision_id)
            except Exception as exc:
                for participant, activation in reversed(activated):
                    try:
                        participant.rollback(revision, activation)
                    except Exception:
                        pass
                for participant, payload in reversed(staged):
                    try:
                        participant.discard(revision, payload)
                    except Exception:
                        pass
                self.store.mark_index_failed(revision_id)
                result = RevisionIndexResult(
                    revision_id=revision_id, activated=False,
                    participants=tuple(p.name for p in self.participants),
                    failed_participant=current.name if current else "revision_store",
                    error_type=type(exc).__name__,
                )
                raise RevisionIndexingError(result) from exc
            for participant, payload in reversed(staged):
                try:
                    participant.discard(revision, payload)
                except Exception:
                    # Active state is already durable; stale staging is safe to
                    # clean on the next maintenance pass and must not undo it.
                    pass
            return RevisionIndexResult(
                revision_id=revision_id, activated=True,
                participants=tuple(p.name for p in self.participants),
            )

    def _lock_for(self, key: tuple[str, str, str]) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())


__all__ = [
    "RevisionIndexCoordinator", "RevisionIndexParticipant",
    "RevisionIndexResult", "RevisionIndexingError",
]
