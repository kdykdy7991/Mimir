"""Bounded parent/neighbor context around one authorized child chunk."""

from __future__ import annotations

from typing import Any

from src.application.contracts import ChunkContextRequest, ContractError, to_jsonable
from src.mcp_server.auth.context import current_principal
from src.mcp_server.clients.errors import AccessDeniedError, InvalidRequestError, ResourceNotFoundError
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error
from src.mcp_server.tools.common import client_from_args

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {"type": "string", "minLength": 1},
        "chunk_id": {"type": "string", "minLength": 1},
        "include": {"type": "string", "enum": ["parent", "neighbors", "both"], "default": "both"},
        "before": {"type": "integer", "minimum": 0, "maximum": 10, "default": 1},
        "after": {"type": "integer", "minimum": 0, "maximum": 10, "default": 1},
        "max_chars": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 12000},
    },
    "required": ["document_id", "chunk_id"], "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {"type": "string"}, "hit": {"type": "object"},
        "parent": {"type": ["object", "null"]},
        "neighbors": {"type": "array", "items": {"type": "object"}},
        "truncated": {"type": "boolean"},
    },
    "required": ["document_id", "hit", "parent", "neighbors", "truncated"],
}


async def _get_chunk_context(args: dict[str, Any]) -> Any:
    try:
        request = ChunkContextRequest(
            document_id=args.get("document_id", ""), chunk_id=args.get("chunk_id", ""),
            include=args.get("include", "both"), before=args.get("before", 1),
            after=args.get("after", 1), max_chars=args.get("max_chars", 12000),
        )
    except (ContractError, TypeError, ValueError) as exc:
        return tool_error(str(exc))
    try:
        result = client_from_args(args).get_chunk_context(request, current_principal())
    except (ResourceNotFoundError, AccessDeniedError):
        return tool_error("chunk not found or not accessible")
    except InvalidRequestError as exc:
        return tool_error(str(exc))
    rows = [result.hit]
    if result.parent is not None:
        rows.append(result.parent)
    rows.extend(result.neighbors)
    markdown = ["# Chunk context", ""]
    for row in rows:
        markdown.append(f"## {row.relation}: `{row.chunk_id}`\n\n{row.text}")
    if result.truncated:
        markdown.append("\n_Context truncated to max_chars._")
    return "\n\n".join(markdown), to_jsonable(result)


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="get_chunk_context",
        description="Read bounded parent and neighboring context for one authorized chunk.",
        input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMA,
        handler=_get_chunk_context,
    )


__all__ = ["register"]
