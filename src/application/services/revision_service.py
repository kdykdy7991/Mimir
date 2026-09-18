"""Authorized revision history, detail and bounded line diff service."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import uuid4

from src.application.contracts import (
    ChunkRevision,
    RevisionDiff,
    RevisionStatus,
    RevisionSummary,
)
from src.ingestion.storage.revision_store import RevisionStore, RevisionStoreError


EDITABLE_METADATA_FIELDS = frozenset({"heading_path", "section", "content_type"})


class RevisionNotFoundOrAccessibleError(RuntimeError):
    """Deliberately hides whether a document, chunk or revision exists."""


@dataclass(frozen=True)
class AuthorizedChunkSnapshot:
    collection: str
    document_id: str
    chunk_id: str
    text: str
    metadata: dict[str, Any]


AuthorizedChunkReader = Callable[[str, str, Any], AuthorizedChunkSnapshot]


class RevisionService:
    def __init__(
        self, store: RevisionStore, authorized_chunk_reader: AuthorizedChunkReader,
        *, max_diff_input_chars: int = 100_000,
        max_diff_output_chars: int = 20_000,
    ) -> None:
        if max_diff_input_chars < 1 or max_diff_output_chars < 1:
            raise ValueError("diff budgets must be positive")
        self.store = store
        self.authorized_chunk_reader = authorized_chunk_reader
        self.max_diff_input_chars = max_diff_input_chars
        self.max_diff_output_chars = max_diff_output_chars

    def list_history(
        self, document_id: str, chunk_id: str, principal: Any,
    ) -> tuple[RevisionSummary, ...]:
        snapshot = self._snapshot(document_id, chunk_id, principal)
        self._ensure_initial(snapshot)
        active = self.store.active(snapshot.collection, document_id, chunk_id)
        active_id = active.revision_id if active else None
        return tuple(
            RevisionSummary(
                revision_id=revision.revision_id,
                base_revision_id=revision.base_revision_id,
                source=revision.source,
                status=status,
                reason=revision.reason,
                actor=revision.actor,
                created_at=revision.created_at,
                checksum_sha256=revision.checksum_sha256,
                is_current=revision.revision_id == active_id,
            )
            for revision, status in self.store.history(
                snapshot.collection, document_id, chunk_id,
            )
        )

    def get_revision(
        self, document_id: str, chunk_id: str, revision_id: str, principal: Any,
    ) -> tuple[ChunkRevision, RevisionStatus, bool]:
        snapshot = self._snapshot(document_id, chunk_id, principal)
        self._ensure_initial(snapshot)
        revision = self._scoped_revision(snapshot, revision_id)
        status = self.store.status(revision_id)
        active = self.store.active(snapshot.collection, document_id, chunk_id)
        return revision, status, bool(active and active.revision_id == revision_id)

    def diff(
        self, document_id: str, chunk_id: str, from_revision_id: str,
        to_revision_id: str, principal: Any,
    ) -> RevisionDiff:
        snapshot = self._snapshot(document_id, chunk_id, principal)
        self._ensure_initial(snapshot)
        before = self._scoped_revision(snapshot, from_revision_id)
        after = self._scoped_revision(snapshot, to_revision_id)
        truncated = False
        before_text = before.text
        after_text = after.text
        if len(before_text) > self.max_diff_input_chars:
            before_text = before_text[:self.max_diff_input_chars]
            truncated = True
        if len(after_text) > self.max_diff_input_chars:
            after_text = after_text[:self.max_diff_input_chars]
            truncated = True
        output = "".join(difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=from_revision_id, tofile=to_revision_id,
        ))
        if len(output) > self.max_diff_output_chars:
            output = output[:self.max_diff_output_chars]
            truncated = True
        return RevisionDiff(
            from_revision_id=from_revision_id,
            to_revision_id=to_revision_id,
            unified_diff=output,
            truncated=truncated,
        )

    def create_edit(
        self, document_id: str, chunk_id: str, principal: Any, *,
        base_revision_id: str, actor: str, reason: str,
        text: str | None = None,
        metadata_patch: Mapping[str, Any] | None = None,
    ) -> ChunkRevision:
        snapshot = self._snapshot(document_id, chunk_id, principal)
        self._ensure_initial(snapshot)
        active = self.store.active(snapshot.collection, document_id, chunk_id)
        if active is None:
            raise RevisionNotFoundOrAccessibleError(
                "revision not found or not accessible",
            )
        patch = dict(metadata_patch or {})
        forbidden = sorted(set(patch) - EDITABLE_METADATA_FIELDS)
        if forbidden:
            raise ValueError(
                f"metadata fields are not editable: {', '.join(forbidden)}",
            )
        new_text = active.text if text is None else text
        if not isinstance(new_text, str):
            raise ValueError("text must be a string")
        metadata = dict(active.metadata)
        metadata.update(patch)
        if new_text == active.text and metadata == dict(active.metadata):
            raise ValueError("edit must change text or editable metadata")
        revision = ChunkRevision(
            revision_id=f"rev_{uuid4().hex}",
            collection=snapshot.collection, document_id=document_id,
            chunk_id=chunk_id, text=new_text, metadata=metadata,
            base_revision_id=base_revision_id, source="edit",
            reason=reason, actor=actor,
        )
        self.store.add_pending(revision)
        return revision

    def create_rollback(
        self, document_id: str, chunk_id: str, principal: Any, *,
        base_revision_id: str, target_revision_id: str,
        actor: str, reason: str,
    ) -> ChunkRevision:
        snapshot = self._snapshot(document_id, chunk_id, principal)
        self._ensure_initial(snapshot)
        target = self._scoped_revision(snapshot, target_revision_id)
        revision = ChunkRevision(
            revision_id=f"rev_{uuid4().hex}",
            collection=snapshot.collection, document_id=document_id,
            chunk_id=chunk_id, text=target.text, metadata=target.metadata,
            base_revision_id=base_revision_id, source="rollback",
            rollback_target_revision_id=target_revision_id,
            reason=reason, actor=actor,
        )
        self.store.add_pending(revision)
        return revision

    def _snapshot(self, document_id: str, chunk_id: str, principal: Any) -> AuthorizedChunkSnapshot:
        try:
            snapshot = self.authorized_chunk_reader(document_id, chunk_id, principal)
        except Exception as exc:  # authorization/not-found stays deliberately uniform
            raise RevisionNotFoundOrAccessibleError(
                "chunk not found or not accessible",
            ) from exc
        if snapshot.document_id != document_id or snapshot.chunk_id != chunk_id:
            raise RevisionNotFoundOrAccessibleError("chunk not found or not accessible")
        return snapshot

    def _ensure_initial(self, snapshot: AuthorizedChunkSnapshot) -> None:
        self.store.ensure_initial(
            collection=snapshot.collection,
            document_id=snapshot.document_id,
            chunk_id=snapshot.chunk_id,
            text=snapshot.text,
            metadata=snapshot.metadata,
        )

    def _scoped_revision(
        self, snapshot: AuthorizedChunkSnapshot, revision_id: str,
    ) -> ChunkRevision:
        try:
            revision = self.store.get(revision_id)
        except RevisionStoreError as exc:
            raise RevisionNotFoundOrAccessibleError(
                "revision not found or not accessible",
            ) from exc
        if (
            revision.collection, revision.document_id, revision.chunk_id,
        ) != (
            snapshot.collection, snapshot.document_id, snapshot.chunk_id,
        ):
            raise RevisionNotFoundOrAccessibleError(
                "revision not found or not accessible",
            )
        return revision


__all__ = [
    "AuthorizedChunkSnapshot", "EDITABLE_METADATA_FIELDS",
    "RevisionNotFoundOrAccessibleError",
    "RevisionService",
]
