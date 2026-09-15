"""EvidenceFilter v1 — governance filters for retrieval (Task 02 §02.1).

The filter is a *contract type only* in Task 02: no retrieval code
consumes it yet (that lands in Task 04). Defining and validating it now
freezes the vocabulary before new tools appear.

Semantics (frozen by the task book):

* a missing field (``None``) means "no restriction on this dimension";
* an **explicit empty list is illegal** — callers must omit the field
  rather than accidentally narrowing a search to nothing;
* list inputs are de-duplicated in deterministic first-occurrence order;
* ``include_descendants`` is only meaningful together with ``folder_id``;
* time bounds must carry timezone offsets.
"""

from __future__ import annotations

import datetime as _dt
import enum
from dataclasses import dataclass
from typing import Any

from src.application.contracts.serialization import (
    ContractError,
    known_kwargs,
    optional_bool,
    optional_str,
    parse_datetime,
    string_tuple,
    to_json,
    to_jsonable,
)


class TagOperator(str, enum.Enum):
    """How multiple ``tag_ids`` combine."""

    AND = "and"
    OR = "or"

    @classmethod
    def coerce(cls, value: Any) -> TagOperator:
        if isinstance(value, TagOperator):
            return value
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError:
                pass
        raise ContractError(
            f"tag_operator must be 'and' or 'or', got {value!r}",
        )


@dataclass(frozen=True)
class EvidenceFilterV1:
    """Governance restrictions applied on top of a retrieval request."""

    collection_ids: tuple[str, ...] | None = None
    document_ids: tuple[str, ...] | None = None
    tag_ids: tuple[str, ...] | None = None
    tag_operator: TagOperator = TagOperator.AND
    folder_id: str | None = None
    include_descendants: bool = False
    file_types: tuple[str, ...] | None = None
    content_types: tuple[str, ...] | None = None
    source_types: tuple[str, ...] | None = None
    updated_after: _dt.datetime | None = None
    updated_before: _dt.datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "collection_ids",
            string_tuple(self.collection_ids, "collection_ids"),
        )
        object.__setattr__(
            self, "document_ids",
            string_tuple(self.document_ids, "document_ids"),
        )
        object.__setattr__(
            self, "tag_ids",
            string_tuple(self.tag_ids, "tag_ids"),
        )
        object.__setattr__(self, "tag_operator", TagOperator.coerce(self.tag_operator))
        object.__setattr__(self, "folder_id", optional_str(self.folder_id, "folder_id"))
        object.__setattr__(
            self, "include_descendants",
            optional_bool(
                self.include_descendants, "include_descendants", default=False,
            ),
        )
        if self.include_descendants and not self.folder_id:
            raise ContractError(
                "include_descendants is only allowed when folder_id is set",
            )
        for name in ("file_types", "content_types", "source_types"):
            object.__setattr__(
                self, name, string_tuple(getattr(self, name), name),
            )
        after = parse_datetime(self.updated_after, "updated_after")
        before = parse_datetime(self.updated_before, "updated_before")
        if after is not None and before is not None and after > before:
            raise ContractError(
                "updated_after must be earlier than or equal to updated_before",
            )
        object.__setattr__(self, "updated_after", after)
        object.__setattr__(self, "updated_before", before)

    @classmethod
    def from_mapping(cls, data: Any) -> EvidenceFilterV1:
        """Build from JSON-safe data, ignoring unknown (newer) filter keys."""
        if isinstance(data, EvidenceFilterV1):
            return data
        kwargs = known_kwargs(cls, data)
        for name in ("updated_after", "updated_before"):
            if name in kwargs:
                kwargs[name] = parse_datetime(kwargs[name], name)
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_json(self) -> str:
        return to_json(self)
