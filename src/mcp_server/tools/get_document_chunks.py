"""
``get_document_chunks`` tool (Phase 3, §5.4).

Read-only, stable-ordered, paginated access to a document's indexed
chunks. Resolution, authorization, stable ordering and paging all happen
in the :class:`RagReadOnlyClient`; this handler only validates → calls →
formats. Ordering is independent of the vector store's natural order and
works for legacy records that lack ``chunk_index``.
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

INPUT_SCHEMA: dict[str, Any] = {
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
        "page": {
            "type": "integer",
            "minimum": 1,
            "default": 1,
            "description": "1-based page number.",
        },
        "page_size": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 20,
            "description": "Chunks per page (1..50).",
        },
    },
    "oneOf": [
        {"required": ["document_id"]},
        {"required": ["doc_id"]},
    ],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_id": {"type": "string"},
        "page": {"type": "integer"},
        "page_size": {"type": "integer"},
        "total": {"type": "integer"},
        "has_next": {"type": "boolean"},
        "chunks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string"},
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                    "page": {"type": ["integer", "null"]},
                    "section": {"type": "string"},
                },
                "required": ["chunk_id", "index"],
            },
        },
    },
    "required": [
        "document_id", "page", "page_size", "total", "has_next", "chunks",
    ],
}

_NOT_FOUND = "document not found or not accessible"


def _resolve_document_id(args: dict[str, Any]) -> str | None:
    raw = args.get("document_id")
    if raw is None:
        raw = args.get("doc_id")
    value = str(raw).strip() if raw is not None else ""
    return value or None


def _render(page) -> dict[str, Any]:
    return {
        "document_id": page.document_id,
        "page": page.page,
        "page_size": page.page_size,
        "total": page.total,
        "has_next": page.has_next,
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "index": c.index,
                "text": c.text,
                "page": c.page,
                "section": c.section,
            }
            for c in page.chunks
        ],
    }


async def _get_document_chunks(args: dict[str, Any]) -> Any:
    document_id = _resolve_document_id(args)
    if not document_id:
        return tool_error(
            "'document_id' is required and must be a non-empty string",
        )
    try:
        page = int(args.get("page") or 1)
        page_size = int(args.get("page_size") or 20)
    except (TypeError, ValueError):
        return tool_error("'page' and 'page_size' must be integers")

    client = client_from_args(args)
    try:
        result = client.get_document_chunks(
            document_id, page, page_size, current_principal(),
        )
    except (ResourceNotFoundError, AccessDeniedError):
        return tool_error(_NOT_FOUND)
    except InvalidRequestError as exc:
        return tool_error(str(exc))
    return _markdown(result), _render(result)


def _markdown(page) -> str:
    lines = [
        f"# Document chunks",
        f"document_id: `{page.document_id}` · page {page.page}/{max((page.total + page.page_size - 1) // page.page_size, 1)}"
        f" · {len(page.chunks)} of {page.total} chunks",
        "",
    ]
    for c in page.chunks:
        loc = f" · page {c.page}" if c.page is not None else ""
        sec = f" · {c.section}" if c.section else ""
        lines.append(
            f"### [{c.index}] `{c.chunk_id}`{loc}{sec}\n\n{c.text or '(empty)'}",
        )
        lines.append("")
    if not page.chunks:
        lines.append("_No chunks on this page._")
    return "\n".join(lines)


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="get_document_chunks",
        description=(
            "Read a stable-ordered page of a document's indexed chunks by "
            "its document_id. Ordering is independent of storage order and "
            "stable across calls. Reads only — never mutates."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_get_document_chunks,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]