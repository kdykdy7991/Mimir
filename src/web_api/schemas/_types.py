"""
Shared annotated types for the schema layer.

Single source of truth for cross-cutting field rules (UTC datetimes, IDs).

Why a dedicated module? A few cross-cutting rules (timezone policy,
ID format) apply to many schemas. Lifting them out of any single
schema keeps the rules consistent and the schemas readable.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from pydantic import AwareDatetime, BeforeValidator, PlainSerializer
from pydantic.json_schema import WithJsonSchema

# ---------------------------------------------------------------------------
# ID type
# ---------------------------------------------------------------------------
# A is allowed to use the generated TypeScript types and we want one
# canonical ID representation in the OpenAPI schema. Pydantic's UUID
# type validates format and serializes to a lowercase string in JSON,
# matching the v0.1 contract: "UUID 字符串" (see PRODUCTION §"统一数据约定").
# ---------------------------------------------------------------------------
Id = UUID
"""Type alias for resource identifiers. Always serialized as a UUID string."""


# ---------------------------------------------------------------------------
# UTC datetime serialised with the "Z" suffix
# ---------------------------------------------------------------------------
# v0.1 contract requires ``"2026-07-31T08:23:11.234Z"`` — Pydantic v2's
# default datetime serialisation uses ``+00:00`` for UTC instants, which
# is valid ISO 8601 but not what we promised. ``UtcDatetime`` forces a
# timezone-aware datetime and emits the ``Z`` suffix in JSON.
#
# The Python value (in handlers / services) is a normal ``datetime``;
# only the JSON wire format changes.
# ---------------------------------------------------------------------------

_UTC_Z_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _serialize_utc(value: datetime) -> str:
    """Serialise a timezone-aware datetime as ``YYYY-MM-DDTHH:MM:SS.fffZ``.

    Millisecond precision matches the example in the v0.1 contract.
    Naive datetimes are rejected — the validator below coerces or fails
    so we never emit ambiguous instants.
    """
    if value.tzinfo is None:
        # Pydantic's ``AwareDatetime`` should already enforce this, but be
        # explicit: a naive datetime is meaningless in the API.
        raise ValueError("UtcDatetime requires a timezone-aware value")
    instant = value.astimezone(timezone.utc)
    return f"{instant.strftime(_UTC_Z_FORMAT)}.{instant.microsecond // 1000:03d}Z"


UtcDatetime = Annotated[
    AwareDatetime,
    PlainSerializer(_serialize_utc, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]
"""Timezone-aware datetime serialised as ``...Z`` in JSON.

The explicit ``WithJsonSchema`` keeps ``format: date-time`` in the
OpenAPI output even though the runtime serializer emits a string —
otherwise Pydantic collapses the field to ``{"type": "string"}`` once
the ``PlainSerializer(return_type=str)`` kicks in. With this
annotation generated TypeScript types declare a proper ISO-8601
string and tools like ``openapi-typescript`` map them correctly."""


# ---------------------------------------------------------------------------
# Chunk ID — a stable opaque string (not a UUID).
# ---------------------------------------------------------------------------
# Chunks in the existing pipeline are identified by
# ``hash(source_path + section_path + content_hash)`` — see
# ``src/ingestion/embedding/`` chunking logic. Keeping it as ``str`` here
# means the API matches what the indexer emits without forcing a UUID
# re-keying in M2.
# ---------------------------------------------------------------------------
_ChunkIdPattern = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _validate_chunk_id(value: Any) -> Any:
    """Light sanity check on chunk IDs — opaque to the API but must be
    safe to embed in URLs and JSON without escaping."""
    if isinstance(value, str) and _ChunkIdPattern.fullmatch(value):
        return value
    raise ValueError(
        "chunk_id must match [A-Za-z0-9_-]{1,128}; opaque hash identifier",
    )


ChunkId = Annotated[str, BeforeValidator(_validate_chunk_id)]
"""Stable chunk identifier — opaque hash, not a UUID."""


__all__ = ["ChunkId", "Id", "UtcDatetime"]
