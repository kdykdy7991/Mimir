"""
E4: ``list_collections`` tool.

Lists the knowledge bases available to the current principal. The
handler is thin: it resolves a :class:`RagReadOnlyClient`, asks for the
authorized collections, and formats the result.

P2.1 normalised contract (§5.1): internal disk paths / Chroma collection
names are removed; each entry carries ``name`` / ``description`` /
``document_count`` / ``chunk_count``. ``count`` is canonical and the
legacy ``n_collections`` is kept during the compatibility window.
"""

from __future__ import annotations

from typing import Any

from src.mcp_server.auth.context import current_principal
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools.common import client_from_args

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "count": {"type": "integer"},
        "n_collections": {
            "type": "integer",
            "description": "Deprecated alias for `count`.",
        },
        "collections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": ["string", "null"]},
                    "document_count": {"type": ["integer", "null"]},
                    "chunk_count": {"type": ["integer", "null"]},
                },
                "required": ["name"],
            },
        },
    },
    "required": ["count", "collections"],
}


def _render(collections) -> tuple[str, dict[str, Any]]:
    n = len(collections)
    md_lines = [f"# Collections ({n})", ""]
    for c in collections:
        bits = [f"**{c.name}**"]
        if c.description:
            bits.insert(1, c.description)
        parts = []
        if c.document_count is not None:
            parts.append(f"{c.document_count} documents")
        if c.chunk_count is not None:
            parts.append(f"{c.chunk_count} chunks")
        if parts:
            bits.append(", ".join(parts))
        md_lines.append("- " + " · ".join(bits))
    if not collections:
        md_lines.append(
            "_No collections found. Run `python scripts/ingest.py "
            "--path <pdf> --collection <name>` to create one._",
        )
    structured = {
        "count": n,
        "n_collections": n,
        "collections": [
            {
                "name": c.name,
                "description": c.description,
                "document_count": c.document_count,
                "chunk_count": c.chunk_count,
            }
            for c in collections
        ],
    }
    return "\n".join(md_lines), structured


async def _list_collections(args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    client = client_from_args(args)
    collections = client.list_collections(current_principal())
    return _render(collections)


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="list_collections",
        description=(
            "List knowledge bases authorized for the current MCP API key. "
            "Each entry includes its collection name, business description, "
            "and document / chunk counts."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_list_collections,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]