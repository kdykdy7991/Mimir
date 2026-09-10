"""
E5: ``get_document_summary`` tool.

Returns read-only metadata for a document identified by its stable UUID
(``doc_id``). The handler is thin: validate ``doc_id`` → call the
:class:`RagReadOnlyClient` → format the :class:`DocumentInfo`. All
document UUID resolution, collection authorization and chunk/vector
reads live in the client (P1.2), never the handler.

Phase-1 note: the doc-detail tool keeps its legacy name and fields here
for compatibility; P2.3 registers ``get_document`` and turns this into a
shared-handler compatibility alias.
"""

from __future__ import annotations

from typing import Any

from src.mcp_server.auth.context import current_principal
from src.mcp_server.clients.errors import (
    AccessDeniedError,
    InvalidRequestError,
    ResourceNotFoundError,
)
from src.mcp_server.protocol_handler import (
    ProtocolHandler,
    tool_error,
)
from src.mcp_server.tools.common import client_from_args

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_id": {
            "type": "string",
            "description": (
                "The document id (= source_ref on every chunk). "
                "Matches the parent Document.id generated at ingest."
            ),
        },
    },
    "required": ["doc_id"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_id": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "source_path": {"type": "string"},
        "doc_type": {"type": "string"},
        "chunk_count": {"type": "integer"},
    },
    "required": [
        "doc_id", "title", "summary", "tags", "source_path",
        "doc_type", "chunk_count",
    ],
}


def _render(info) -> tuple[str, dict[str, Any]]:
    structured = {
        "doc_id": info.document_id,
        "title": info.title,
        "summary": info.summary,
        "tags": list(info.tags),
        "source_path": info.source or "",
        "doc_type": info.document_type,
        "chunk_count": int(info.chunk_count),
    }
    md_lines = [
        f"# {structured['title']}",
        "",
        f"**doc_id**: `{structured['doc_id']}`",
        f"**source**: {structured['source_path'] or '(unknown)'}",
        f"**doc_type**: {structured['doc_type'] or '(unknown)'}",
        f"**chunks**: {structured['chunk_count']}",
    ]
    if structured["tags"]:
        md_lines.append(f"**tags**: {', '.join(structured['tags'])}")
    if structured["summary"]:
        md_lines += ["", "## Summary", "", structured["summary"]]
    return "\n".join(md_lines), structured


async def _get_document_summary(args: dict[str, Any]) -> Any:
    doc_id = (args.get("doc_id") or "").strip()
    if not doc_id:
        return tool_error(
            "'doc_id' is required and must be a non-empty string",
        )
    client = client_from_args(args)
    try:
        info = client.get_document(doc_id, current_principal())
    except ResourceNotFoundError:
        return tool_error(
            f"document not found: doc_id={doc_id!r}. "
            "Confirm the id is correct and the document has been ingested.",
        )
    except AccessDeniedError:
        # Do not let a valid-but-forbidden UUID probe another collection.
        return tool_error("document not found or not accessible")
    except InvalidRequestError as exc:
        return tool_error(str(exc))
    return _render(info)


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="get_document_summary",
        description=(
            "Return a document summary from an authorized SKDY knowledge base by its doc_id."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_get_document_summary,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]