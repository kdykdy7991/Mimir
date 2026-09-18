"""Contracts shared by RSS, controlled URL and future connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Protocol


@dataclass(frozen=True)
class SyncCheckpoint:
    cursor: str | None = None
    revision: int = 0
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ConnectorItem:
    external_id: str
    revision: str
    deleted: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceDocument:
    external_id: str
    revision: str
    title: str
    content: bytes
    media_type: str
    source_uri: str
    checksum_sha256: str
    deleted: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    def test(self) -> None: ...
    def list(self, checkpoint: SyncCheckpoint | None) -> Iterable[ConnectorItem]: ...
    def fetch(self, item: ConnectorItem) -> SourceDocument: ...
    def checkpoint(self) -> SyncCheckpoint: ...
    def close(self) -> None: ...


__all__ = ["Connector", "ConnectorItem", "SourceDocument", "SyncCheckpoint"]
