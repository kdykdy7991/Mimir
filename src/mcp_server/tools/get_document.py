"""
``get_document`` — canonical read-only document detail tool (P2.3).

Returns metadata for a document identified by a stable document UUID.
Resolution, collection authorization and chunk reads live in the
:class:`RagReadOnlyClient`; this handler only validates → calls the
client → formats.

P2.3 (§5.3):

- Canonical param is ``document_id``; ``doc_id`` is accepted as a
  compatibility alias (shared handler with ``get_document_summary``).
- Output exposes both the canonical fields (``document_id`` /
  ``collection`` / ``document_type`` / ``source``) and the legacy aliases
  (``doc_id`` / ``doc_type`` / ``source_path``).
- A document that does not exist and one the principal may not read both
  resolve to ``document not found or not accessible`` (no resource-presence
  leakage).
"""

from __future__ import annotations

from typing import Any

from src.mcp_server.auth.context import current_principal
from src.mcp_server.clients.errors import (
    AccessDeniedError,
    InvalidRequestError,
    ResourceNotFoundError,
)
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error
from src.mcp_server.tools.common import client_from_args

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # canonical
        "document_id": {"type": "string"},
        "collection": {"type": "string"},
        "title": {"type": "string"},
        "document_type": {"type": "string"},
        "source": {"type": "string"},
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "chunk_count": {"type": "integer"},
        # legacy aliases (get_document_summary compatibility)
        "doc_id": {"type": "string"},
        "doc_type": {"type": "string"},
        "source_path": {"type": "string"},
    },
    "required": [
        "document_id", "title", "summary", "tags",
        "source", "doc_type", "chunk_count",
    ],
}

_NOT_FOUND = "document not found or not accessible"


def _resolve_document_id(args: dict[str, Any]) -> str | None:
    """Resolve ``document_id`` (canonical) or ``doc_id`` (legacy)."""
    raw = args.get("document_id")
    if raw is None:
        raw = args.get("doc_id")
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _render(info) -> tuple[str, dict[str, Any]]:
    structured = {
        # canonical
        "document_id": info.document_id,
        "collection": info.collection,
        "title": info.title,
        "document_type": info.document_type,
        "source": info.source or "",
        "summary": info.summary,
        "tags": list(info.tags),
        "chunk_count": int(info.chunk_count),
        # legacy aliases
        "doc_id": info.document_id,
        "doc_type": info.document_type,
        "source_path": info.source or "",
    }
    md_lines = [
        f"# {structured['title']}",
        "",
        f"**document_id**: `{structured['document_id']}`",
        f"**source**: {structured['source'] or '(unknown)'}",
        f"**doc_type**: {structured['doc_type'] or '(unknown)'}",
        f"**chunks**: {structured['chunk_count']}",
    ]
    if structured["tags"]:
        md_lines.append(f"**tags**: {', '.join(structured['tags'])}")
    if structured["summary"]:
        md_lines += ["", "## Summary", "", structured["summary"]]
    return "\n".join(md_lines), structured


async def _get_document_item(args: dict[str, Any]) -> Any:
    document_id = _resolve_document_id(args)
    if not document_id:
        return tool_error(
            "'document_id' is required and must be a non-empty string",
        )
    client = client_from_args(args)
    try:
        info = client.get_document(document_id, current_principal())
    except (ResourceNotFoundError, AccessDeniedError):
        # Unify not-found and forbidden into one non-revealing message.
        return tool_error(_NOT_FOUND)
    except InvalidRequestError as exc:
        return tool_error(str(exc))
    return _render(info)


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="get_document",
        description=(
            "Return read-only metadata for a document (by its document_id) "
            "from an authorized SKDY knowledge base, including its title, "
            "summary, tags, source and chunk count."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The stable document UUID.",
                },
                "doc_id": {
                    "type": "string",
                    "description": "Deprecated alias for `document_id`.",
                },
            },
            "required": ["document_id"],
            "oneOf": [
                {"required": ["document_id"]},
                {"required": ["doc_id"]},
            ],
            "additionalProperties": False,
        },
        handler=_get_document_item,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["OUTPUT_SCHEMA", "_NOT_FOUND", "_get_document_item", "register"]