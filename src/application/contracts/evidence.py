"""Evidence v1 — transport-neutral knowledge evidence (Task 02 §02.1).

The contract answers: what is one piece of provenance-bearing evidence,
independently of whether it is delivered over MCP, REST or an in-process
call. The MCP presentation layer maps this to structured content in
02.2; the application layer never imports that mapper.

Design notes
------------
* ``scores`` always carries all four stages as a fixed-shape object;
  an unexecuted stage is ``None`` (→ ``null``), never ``0`` and never
  guessed from the legacy single ``score``.
* ``source_locator`` is *relative* provenance (kind/page/heading);
  absolute disk paths are rejected at construction.
* optional future fields (versions, parent, assets, ...) are ``None``
  by default and MUST NOT be fabricated from legacy data (Task 03+).
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass
from typing import Any

from src.application.contracts.serialization import (
    ContractError,
    known_kwargs,
    optional_int,
    optional_str,
    parse_datetime,
    require_nonempty_str,
    string_tuple,
    to_json,
    to_jsonable,
)

# A locator ``kind`` is a short vocabulary token (e.g. "page", "section",
# "table"). It must never be used to smuggle an absolute storage path.
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def _finite_optional_score(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"scores.{name} must be a number or null")
    number = float(value)
    if not math.isfinite(number):
        raise ContractError(f"scores.{name} must be finite, got {value!r}")
    return number


@dataclass(frozen=True)
class EvidenceScores:
    """Per-stage retrieval scores. ``None`` means the stage did not run."""

    dense: float | None = None
    sparse: float | None = None
    fusion: float | None = None
    rerank: float | None = None

    def __post_init__(self) -> None:
        for name in ("dense", "sparse", "fusion", "rerank"):
            object.__setattr__(
                self, name, _finite_optional_score(getattr(self, name), name),
            )

    @classmethod
    def from_mapping(cls, data: Any) -> EvidenceScores:
        """Build from plain data, ignoring unknown (newer) stage keys."""
        if isinstance(data, EvidenceScores):
            return data
        kwargs = known_kwargs(cls, data)
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_json(self) -> str:
        return to_json(self)


@dataclass(frozen=True)
class SourceLocator:
    """Relative provenance pointer — kind + optional page/heading.

    Deliberately has no path/URL field: absolute disk paths and internal
    storage coordinates never leave the boundary (ADR 0001 §3.4).
    """

    kind: str
    page: int | None = None
    heading: str | None = None

    def __post_init__(self) -> None:
        kind = require_nonempty_str(self.kind, "source_locator.kind")
        if kind.startswith("/") or _WINDOWS_ABSOLUTE.match(kind):
            raise ContractError(
                "source_locator.kind must be a locator token, not an "
                f"absolute path: {kind!r}",
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self, "page",
            optional_int(self.page, "source_locator.page", minimum=1),
        )
        if self.heading is not None:
            if not isinstance(self.heading, str):
                raise ContractError("source_locator.heading must be a string")
            object.__setattr__(self, "heading", self.heading or None)

    @classmethod
    def from_mapping(cls, data: Any) -> SourceLocator:
        if isinstance(data, SourceLocator):
            return data
        return cls(**known_kwargs(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_json(self) -> str:
        return to_json(self)


# Optional string fields (None unless genuinely known).
_OPTIONAL_ID_FIELDS = (
    "document_version", "chunk_version", "parent_chunk_id",
    "title", "content", "content_preview",
)


@dataclass(frozen=True)
class EvidenceV1:
    """One provenance-bearing evidence chunk (the v1 evidence contract)."""

    collection_id: str
    document_id: str
    chunk_id: str
    content_type: str
    source_locator: SourceLocator
    scores: EvidenceScores
    matched_queries: tuple[str, ...]
    # --- Future-proof optional fields; None until a task fills them in. ---
    document_version: str | None = None
    chunk_version: str | None = None
    parent_chunk_id: str | None = None
    title: str | None = None
    content: str | None = None
    content_preview: str | None = None
    heading_path: tuple[str, ...] | None = None
    asset_ids: tuple[str, ...] | None = None
    indexed_at: _dt.datetime | None = None

    def __post_init__(self) -> None:
        for name in ("collection_id", "document_id", "chunk_id", "content_type"):
            object.__setattr__(self, name, require_nonempty_str(getattr(self, name), name))
        object.__setattr__(
            self, "source_locator",
            SourceLocator.from_mapping(self.source_locator),
        )
        object.__setattr__(self, "scores", EvidenceScores.from_mapping(self.scores))
        queries = string_tuple(self.matched_queries, "matched_queries")
        if not queries:
            raise ContractError("matched_queries must contain at least one query")
        object.__setattr__(self, "matched_queries", queries)
        for name in _OPTIONAL_ID_FIELDS:
            object.__setattr__(self, name, optional_str(getattr(self, name), name))
        object.__setattr__(
            self, "heading_path",
            string_tuple(self.heading_path, "heading_path", allow_empty=False),
        )
        object.__setattr__(
            self, "asset_ids",
            string_tuple(self.asset_ids, "asset_ids", allow_empty=False),
        )
        object.__setattr__(self, "indexed_at", parse_datetime(self.indexed_at, "indexed_at"))

    @classmethod
    def from_mapping(cls, data: Any) -> EvidenceV1:
        """Build from JSON-safe data.

        Unknown optional keys (added by a newer server) are ignored — see
        :mod:`serialization` forward-compatibility policy.
        """
        if isinstance(data, EvidenceV1):
            return data
        kwargs = known_kwargs(cls, data)
        # Nested values arrive as mappings/strings and are re-validated by
        # __post_init__, so from_mapping only needs the datetimes handled.
        if "indexed_at" in kwargs:
            kwargs["indexed_at"] = parse_datetime(kwargs["indexed_at"], "indexed_at")
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_json(self) -> str:
        return to_json(self)
