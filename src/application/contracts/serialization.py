"""Single deterministic serialization entry point for v1 contracts.

Every contract type goes through :func:`to_jsonable` / :func:`to_json` —
there is deliberately no per-model ``json.dumps`` call. Rules:

* datetimes are emitted as timezone-aware ISO 8601 (naive datetimes are
  rejected at construction, never auto-stamped with a local zone);
* ``None`` serializes to JSON ``null`` — unexecuted pipeline stages are
  ``null``, never omitted or faked as ``0``;
* floats must be finite (``NaN``/``Infinity`` are not valid JSON);
* field order is declaration order, so output is byte-deterministic
  without relying on key sorting;
* ``ensure_ascii=False`` so non-ASCII evidence is not escaped;
* unknown keys on *read* (:func:`known_kwargs`) are ignored — the
  documented forward-compatibility strategy for optional fields added
  by newer servers. They are never re-emitted.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Iterable


class ContractError(ValueError):
    """Raised when a v1 contract value is constructed or serialized
    from invalid data (empty restrictions, naive datetimes, etc.)."""


# ---------------------------------------------------------------------------
# JSON-safe conversion — the ONE place that knows how contract values
# become plain ``dict``/``list``/scalars.
# ---------------------------------------------------------------------------

def to_jsonable(value: Any) -> Any:
    """Convert a contract value (or collection of them) to JSON-safe data."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ContractError(
                f"non-finite numeric value is not JSON-safe: {value!r}",
            )
        return value
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ContractError(
                "datetime values must carry an explicit timezone",
            )
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    raise ContractError(
        f"value of type {type(value).__name__!r} is not JSON-safe in v1 contracts",
    )


def to_json(value: Any) -> str:
    """Deterministic JSON document for a contract value."""
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


# ---------------------------------------------------------------------------
# Construction helpers shared by contract types
# ---------------------------------------------------------------------------

def known_kwargs(model_cls: type, mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the keys that match declared dataclass fields.

    Unknown keys are silently dropped — this is the explicit
    forward-compatibility strategy: a newer server may add optional
    fields; older readers ignore them instead of failing.
    """
    if not isinstance(mapping, Mapping):
        raise ContractError(
            f"expected a mapping to build {model_cls.__name__}, "
            f"got {type(mapping).__name__}",
        )
    names = {field.name for field in dataclasses.fields(model_cls)}
    return {key: val for key, val in mapping.items() if key in names}


def require_nonempty_str(value: Any, field_name: str) -> str:
    """Validate a required non-empty string field."""
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string")
    return value


def optional_str(value: Any, field_name: str) -> str | None:
    """Validate an optional string (``None`` or non-empty string)."""
    if value is None:
        return None
    return require_nonempty_str(value, field_name)


def optional_bool(value: Any, field_name: str, *, default: bool) -> bool:
    """Validate a boolean that may arrive as ``None`` (→ default)."""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(f"{field_name} must be a boolean")
    return value


def optional_int(value: Any, field_name: str, *, minimum: int | None = None) -> int | None:
    """Validate an optional positive integer (booleans rejected)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ContractError(f"{field_name} must be >= {minimum}")
    return value


def string_tuple(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = False,
    sort: bool = False,
) -> tuple[str, ...] | None:
    """Coerce a list/tuple of non-empty strings into a deduped tuple.

    ``None`` means "field absent" (no restriction / no value) and is
    preserved. An explicit empty collection is rejected unless
    ``allow_empty`` is set. Order is first-occurrence order unless
    ``sort`` is requested, so output stays deterministic.
    """
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ContractError(f"{field_name} must be a list of strings")
    items = [require_nonempty_str(v, field_name) for v in value]
    if not items and not allow_empty:
        raise ContractError(
            f"{field_name} must not be an explicit empty list; "
            "omit the field to mean 'no restriction'",
        )
    return deduplicated(items, sort=sort)


def deduplicated(items: Iterable[str], *, sort: bool = False) -> tuple[str, ...]:
    """Return items with duplicates removed, preserving first-occurrence order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return tuple(sorted(ordered) if sort else ordered)


def parse_datetime(value: Any, field_name: str) -> _dt.datetime | None:
    """Parse an ISO 8601 string into a timezone-aware datetime."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = _dt.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ContractError(
                f"{field_name} must be an ISO 8601 datetime: {value!r}",
            ) from exc
    else:
        raise ContractError(
            f"{field_name} must be an ISO 8601 datetime string",
        )
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ContractError(f"{field_name} must include a timezone offset")
    return parsed


def require_aware(value: Any, field_name: str) -> _dt.datetime | None:
    """Validate an optional datetime that is already a ``datetime``."""
    parsed = parse_datetime(value, field_name)
    return parsed
