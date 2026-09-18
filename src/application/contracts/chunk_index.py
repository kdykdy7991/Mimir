"""Versioned parent/child chunk and asset contracts (Task 06)."""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.application.contracts.serialization import ContractError, string_tuple


INDEX_FORMAT_VERSION = 2
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


class ChunkLevel(str, enum.Enum):
    PARENT = "parent"
    CHILD = "child"


class ContextInclude(str, enum.Enum):
    PARENT = "parent"
    NEIGHBORS = "neighbors"
    BOTH = "both"


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int

    def __post_init__(self) -> None:
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (self.start, self.end)):
            raise ContractError("source_span offsets must be integers")
        if self.start < 0 or self.end < self.start:
            raise ContractError("source_span must satisfy 0 <= start <= end")

    @classmethod
    def from_mapping(cls, value: Any) -> "SourceSpan | None":
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ContractError("source_span must be an object")
        return cls(start=value.get("start"), end=value.get("end"))


@dataclass(frozen=True)
class AssetV1:
    asset_id: str
    document_id: str
    chunk_id: str
    document_version: str
    mime_type: str
    byte_size: int
    checksum_sha256: str
    locator: str

    def __post_init__(self) -> None:
        for name in (
            "asset_id", "document_id", "chunk_id", "document_version",
            "mime_type", "checksum_sha256", "locator",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ContractError(f"{name} must be a non-empty string")
        if isinstance(self.byte_size, bool) or not isinstance(self.byte_size, int) or self.byte_size < 0:
            raise ContractError("byte_size must be a non-negative integer")
        if not re.fullmatch(r"[0-9a-f]{64}", self.checksum_sha256):
            raise ContractError("checksum_sha256 must be 64 lowercase hex characters")
        if self.locator.startswith(("/", "\\")) or _WINDOWS_ABSOLUTE.match(self.locator):
            raise ContractError("asset locator must be relative")
        if ".." in self.locator.replace("\\", "/").split("/"):
            raise ContractError("asset locator must not contain path traversal")


@dataclass(frozen=True)
class AssetContent:
    metadata: AssetV1
    data: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, AssetV1):
            raise ContractError("asset metadata must be AssetV1")
        if not isinstance(self.data, bytes):
            raise ContractError("asset data must be bytes")
        if len(self.data) != self.metadata.byte_size:
            raise ContractError("asset byte_size does not match data")
        checksum = hashlib.sha256(self.data).hexdigest()
        if checksum != self.metadata.checksum_sha256:
            raise ContractError("asset checksum_sha256 does not match data")


@dataclass(frozen=True)
class ChunkIndexMetadata:
    """Canonical v2 metadata with fail-soft legacy-v1 reads."""

    index_format_version: int
    chunk_level: ChunkLevel
    parent_chunk_id: str | None
    heading_path: tuple[str, ...]
    source_span: SourceSpan | None
    document_version: str | None
    chunk_version: str | None
    asset_ids: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ChunkIndexMetadata":
        data = dict(value or {})
        version = data.get("index_format_version", 1)
        try:
            version = int(version)
        except (TypeError, ValueError) as exc:
            raise ContractError("index_format_version must be an integer") from exc
        if version < 1 or version > INDEX_FORMAT_VERSION:
            raise ContractError(f"unsupported index_format_version: {version}")
        level_raw = data.get("chunk_level", ChunkLevel.CHILD.value)
        try:
            level = ChunkLevel(str(level_raw))
        except ValueError as exc:
            raise ContractError("chunk_level must be parent or child") from exc
        parent = data.get("parent_chunk_id") or data.get("parent_id") or None
        headings = data.get("heading_path")
        if headings is None:
            legacy_heading = data.get("heading") or data.get("section")
            headings = (legacy_heading,) if legacy_heading else ()
        assets = data.get("asset_ids") or data.get("image_ids") or ()
        if isinstance(assets, str):
            assets = tuple(x.strip() for x in assets.split(",") if x.strip())
        return cls(
            index_format_version=version, chunk_level=level,
            parent_chunk_id=str(parent) if parent else None,
            heading_path=string_tuple(
                headings, "heading_path", allow_empty=True,
            ) or (),
            source_span=SourceSpan.from_mapping(data.get("source_span")),
            document_version=(str(data["document_version"]) if data.get("document_version") else None),
            chunk_version=(str(data["chunk_version"]) if data.get("chunk_version") else None),
            asset_ids=string_tuple(
                assets, "asset_ids", allow_empty=True,
            ) or (),
        )


@dataclass(frozen=True)
class ChunkContextRequest:
    document_id: str
    chunk_id: str
    include: ContextInclude = ContextInclude.BOTH
    before: int = 1
    after: int = 1
    max_chars: int = 12_000

    def __post_init__(self) -> None:
        for name in ("document_id", "chunk_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ContractError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        try:
            include = self.include if isinstance(self.include, ContextInclude) else ContextInclude(str(self.include))
        except ValueError as exc:
            raise ContractError("include must be parent, neighbors, or both") from exc
        object.__setattr__(self, "include", include)
        for name in ("before", "after"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
                raise ContractError(f"{name} must be an integer between 0 and 10")
        if isinstance(self.max_chars, bool) or not isinstance(self.max_chars, int) or not 1 <= self.max_chars <= 100_000:
            raise ContractError("max_chars must be an integer between 1 and 100000")


@dataclass(frozen=True)
class ContextChunkV1:
    chunk_id: str
    relation: str
    text: str
    content_type: str = "text"
    source_locator: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ChunkContextResult:
    document_id: str
    hit: ContextChunkV1
    parent: ContextChunkV1 | None = None
    neighbors: tuple[ContextChunkV1, ...] = ()
    truncated: bool = False


def content_version(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_parent_chunk_id(document_id: str, document_version: str, ordinal: int) -> str:
    digest = content_version(document_id, document_version, str(ordinal))[:16]
    return f"{document_id}:parent:{ordinal:04d}:{digest}"


def stable_asset_id(document_id: str, document_version: str, checksum_sha256: str) -> str:
    digest = content_version(document_id, document_version, checksum_sha256)[:20]
    return f"asset:{digest}"


__all__ = [
    "INDEX_FORMAT_VERSION", "AssetContent", "AssetV1", "ChunkContextRequest",
    "ChunkContextResult", "ChunkIndexMetadata", "ChunkLevel", "ContextChunkV1",
    "ContextInclude", "SourceSpan", "content_version", "stable_asset_id",
    "stable_parent_chunk_id",
]
