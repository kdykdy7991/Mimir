"""Exact, ownership-checked chunk read tool (Task 03)."""

from __future__ import annotations

from typing import Any

from src.mcp_server.auth.context import current_principal
from src.mcp_server.clients.errors import AccessDeniedError, InvalidRequestError, ResourceNotFoundError
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error
from src.mcp_server.tools.common import client_from_args

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_id": {"type": "string"}, "chunk_id": {"type": "string"},
        "index": {"type": "integer"}, "text": {"type": "string"},
        "heading": {"type": ["string", "null"]}, "page": {"type": ["integer", "null"]},
        "content_type": {"type": "string"},
        "previous_chunk_id": {"type": ["string", "null"]},
        "next_chunk_id": {"type": ["string", "null"]},
        "parent_id": {"type": ["string", "null"]},
        "source_locator": {"type": "object"},
        "asset_ids": {"type": "array", "items": {"type": "string"}},
        "document_version": {"type": ["string", "null"]},
        "chunk_version": {"type": ["string", "null"]},
        "is_current": {"type": "boolean"},
    },
    "required": ["document_id", "chunk_id", "index", "text", "content_type", "previous_chunk_id", "next_chunk_id", "parent_id", "source_locator", "asset_ids", "document_version", "chunk_version", "is_current"],
}


async def _get_chunk(args: dict[str, Any]) -> Any:
    document_id = str(args.get("document_id") or args.get("doc_id") or "").strip()
    chunk_id = str(args.get("chunk_id") or "").strip()
    if not document_id or not chunk_id:
        return tool_error("'document_id' and 'chunk_id' are required and must be non-empty strings")
    try:
        chunk = client_from_args(args).get_chunk(
            document_id, chunk_id, current_principal(),
        )
    except (ResourceNotFoundError, AccessDeniedError):
        return tool_error("chunk not found or not accessible")
    except InvalidRequestError as exc:
        return tool_error(str(exc))
    expected_version = str(args.get("expected_chunk_version") or "").strip()
    if expected_version and chunk.chunk_version != expected_version:
        return tool_error("stale_reference: requested chunk version is not current")
    structured = {
        "document_id": chunk.document_id, "chunk_id": chunk.chunk_id,
        "index": chunk.index, "text": chunk.text, "heading": chunk.heading,
        "page": chunk.page, "content_type": chunk.content_type,
        "previous_chunk_id": chunk.previous_chunk_id,
        "next_chunk_id": chunk.next_chunk_id, "parent_id": chunk.parent_id,
        "source_locator": chunk.source_locator, "asset_ids": list(chunk.asset_ids),
        "document_version": chunk.document_version,
        "chunk_version": chunk.chunk_version, "is_current": chunk.is_current,
    }
    return f"# Chunk `{chunk.chunk_id}`\n\n{chunk.text}", structured


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="get_chunk",
        description="Read one exact chunk after verifying its document, collection authorization, and chunk ownership.",
        input_schema={
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "doc_id": {"type": "string", "description": "Deprecated alias for document_id."},
                "chunk_id": {"type": "string"},
                "expected_chunk_version": {
                    "type": "string",
                    "description": "Optional version assertion; mismatch returns stale_reference.",
                },
            },
            "oneOf": [{"required": ["document_id", "chunk_id"]}, {"required": ["doc_id", "chunk_id"]}],
            "additionalProperties": False,
        }, handler=_get_chunk, output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]
