"""Warning and pagination/budget envelopes (Task 02 §02.1).

These are the transport-neutral counterparts of the MCP tool-level
diagnostics: degraded retrieval is a *successful* response carrying
warnings, while errors use the separate stable error vocabulary added
in 02.3. Tool-level failure must never masquerade as an empty evidence
page.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from src.application.contracts.evidence import EvidenceV1
from src.application.contracts.serialization import (
    ContractError,
    known_kwargs,
    optional_bool,
    to_json,
    to_jsonable,
)


class WarningCode(str, enum.Enum):
    """Stable warning codes for v1 responses."""

    TRUNCATED = "truncated"
    RERANK_DEGRADED = "rerank_degraded"
    LEGACY_METADATA_MISSING = "legacy_metadata_missing"
    PARTIAL_COLLECTION_FAILURE = "partial_collection_failure"


@dataclass(frozen=True)
class WarningV1:
    """A non-fatal signal attached to a successful evidence response."""

    code: WarningCode
    message: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.code, WarningCode):
            if isinstance(self.code, str):
                try:
                    object.__setattr__(self, "code", WarningCode(self.code))
                except ValueError as exc:
                    raise ContractError(
                        f"unknown warning code: {self.code!r}",
                    ) from exc
            else:
                raise ContractError("warning code must be a WarningCode value")
        if not isinstance(self.message, str):
            raise ContractError("warning message must be a string")
        if not isinstance(self.detail, Mapping):
            raise ContractError("warning detail must be a mapping")
        # Immutable mapping — warnings must not be mutated after construction.
        object.__setattr__(
            self, "detail",
            MappingProxyType(dict(self.detail)),
        )

    @classmethod
    def from_mapping(cls, data: Any) -> WarningV1:
        if isinstance(data, WarningV1):
            return data
        return cls(**known_kwargs(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_json(self) -> str:
        return to_json(self)


@dataclass(frozen=True)
class EvidencePageV1:
    """A bounded window of evidence plus pagination/budget signals.

    Pagination is page-based (``page``/``page_size``) or cursor-based
    (``cursor``/``next_cursor``), never both. ``returned_count`` always
    equals ``len(results)``. Truncation flags are an explicit contract:
    silent truncation is forbidden (Task 02 §02.3).
    """

    results: tuple[EvidenceV1, ...]
    page: int | None = None
    page_size: int | None = None
    cursor: str | None = None
    next_cursor: str | None = None
    has_next: bool = False
    truncated_results: bool = False
    truncated_characters: bool = False
    warnings: tuple[WarningV1, ...] = ()
    returned_count: int = 0

    def __post_init__(self) -> None:
        rows: list[EvidenceV1] = []
        for item in self.results or ():
            rows.append(EvidenceV1.from_mapping(item))
        object.__setattr__(self, "results", tuple(rows))
        paged = self.page is not None or self.page_size is not None
        cursored = self.cursor is not None
        if paged and cursored:
            raise ContractError(
                "page-based and cursor-based pagination are mutually exclusive",
            )
        if paged:
            if self.page is None or self.page_size is None:
                raise ContractError("page and page_size must be provided together")
            if isinstance(self.page, bool) or not isinstance(self.page, int) or self.page < 1:
                raise ContractError("page must be an integer >= 1")
            if (
                isinstance(self.page_size, bool)
                or not isinstance(self.page_size, int)
                or self.page_size < 1
            ):
                raise ContractError("page_size must be an integer >= 1")
        if self.cursor is not None and not (
            isinstance(self.cursor, str) and self.cursor
        ):
            raise ContractError("cursor must be a non-empty string")
        if self.next_cursor is not None and not (
            isinstance(self.next_cursor, str) and self.next_cursor
        ):
            raise ContractError("next_cursor must be a non-empty string")
        object.__setattr__(
            self, "has_next",
            optional_bool(self.has_next, "has_next", default=False),
        )
        object.__setattr__(
            self, "truncated_results",
            optional_bool(self.truncated_results, "truncated_results", default=False),
        )
        object.__setattr__(
            self, "truncated_characters",
            optional_bool(
                self.truncated_characters, "truncated_characters", default=False,
            ),
        )
        warnings = tuple(WarningV1.from_mapping(w) for w in (self.warnings or ()))
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "returned_count", len(rows))
        # Every truncation must be accompanied by an explicit warning code.
        codes = {w.code for w in warnings}
        if self.truncated_results and WarningCode.TRUNCATED not in codes:
            raise ContractError(
                "truncated_results=true requires a 'truncated' warning",
            )
        if self.truncated_characters and WarningCode.TRUNCATED not in codes:
            raise ContractError(
                "truncated_characters=true requires a 'truncated' warning",
            )

    @classmethod
    def from_mapping(cls, data: Any) -> EvidencePageV1:
        if isinstance(data, EvidencePageV1):
            return data
        return cls(**known_kwargs(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_json(self) -> str:
        return to_json(self)
