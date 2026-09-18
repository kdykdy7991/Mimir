"""Immutable knowledge revision contracts (Task 07)."""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from src.application.contracts.serialization import ContractError, require_nonempty_str


class RevisionSource(str, enum.Enum):
    INITIAL = "initial"
    EDIT = "edit"
    ROLLBACK = "rollback"


class RevisionStatus(str, enum.Enum):
    PENDING_INDEX = "pending_index"
    ACTIVE = "active"
    RETIRED = "retired"
    INDEX_FAILED = "index_failed"


def revision_checksum(text: str, metadata: Mapping[str, Any] | None = None) -> str:
    import json

    payload = json.dumps(
        {"text": text, "metadata": dict(metadata or {})},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def legacy_revision_id(
    collection: str, document_id: str, chunk_id: str, checksum: str,
) -> str:
    payload = "\x1f".join((collection, document_id, chunk_id, checksum))
    return f"rev_initial_{hashlib.sha256(payload.encode()).hexdigest()[:24]}"


@dataclass(frozen=True)
class ChunkRevision:
    revision_id: str
    collection: str
    document_id: str
    chunk_id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    base_revision_id: str | None = None
    source: RevisionSource = RevisionSource.EDIT
    reason: str = "unspecified"
    actor: str = "system"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    checksum_sha256: str = ""
    rollback_target_revision_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("revision_id", "collection", "document_id", "chunk_id", "reason", "actor"):
            object.__setattr__(self, name, require_nonempty_str(getattr(self, name), name))
        if not isinstance(self.text, str):
            raise ContractError("revision text must be a string")
        if not isinstance(self.metadata, Mapping):
            raise ContractError("revision metadata must be an object")
        object.__setattr__(self, "metadata", dict(self.metadata))
        try:
            source = self.source if isinstance(self.source, RevisionSource) else RevisionSource(str(self.source))
        except ValueError as exc:
            raise ContractError("invalid revision source") from exc
        object.__setattr__(self, "source", source)
        if self.created_at.tzinfo is None:
            raise ContractError("created_at must be timezone-aware")
        expected = revision_checksum(self.text, self.metadata)
        if self.checksum_sha256 and self.checksum_sha256 != expected:
            raise ContractError("revision checksum does not match content")
        object.__setattr__(self, "checksum_sha256", expected)
        if source is RevisionSource.INITIAL and self.base_revision_id is not None:
            raise ContractError("initial revision cannot have a base revision")
        if source is RevisionSource.ROLLBACK and not self.rollback_target_revision_id:
            raise ContractError("rollback revision requires a target revision")


@dataclass(frozen=True)
class RevisionSummary:
    revision_id: str
    base_revision_id: str | None
    source: RevisionSource
    status: RevisionStatus
    reason: str
    actor: str
    created_at: datetime
    checksum_sha256: str
    is_current: bool


@dataclass(frozen=True)
class RevisionDiff:
    from_revision_id: str
    to_revision_id: str
    unified_diff: str
    truncated: bool = False


__all__ = [
    "ChunkRevision", "RevisionDiff", "RevisionSource", "RevisionStatus",
    "RevisionSummary",
    "legacy_revision_id", "revision_checksum",
]
