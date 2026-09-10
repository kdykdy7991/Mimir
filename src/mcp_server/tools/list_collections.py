"""
E4: ``list_collections`` tool.

Lists the knowledge bases available to the current principal. The
handler is thin: it resolves a :class:`RagReadOnlyClient`, asks for the
authorized collections, and formats the result. All storage / vector
store / settings access lives in the client (see P1.2).

Phase-1 note: the output keeps the pre-normalisation legacy fields
(``source`` / ``bm25_chunks`` / ``vector_count`` / ``data_dir``) so the
migration is behaviour-identical; P2.1 replaces these with the canonical
``document_count`` / ``chunk_count`` contract.
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
        "n_collections": {"type": "integer"},
        "collections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source": {
                        "type": "string",
                        "enum": ["bm25", "vector_store", "both"],
                    },
                    "bm25_chunks": {"type": ["integer", "null"]},
                    "vector_count": {"type": ["integer", "null"]},
                    "data_dir": {"type": "string"},
                    "description": {"type": ["string", "null"]},
                },
                "required": ["name", "source", "data_dir"],
            },
        },
    },
    "required": ["n_collections", "collections"],
}


def _render(collections) -> tuple[str, dict[str, Any]]:
    md_lines = [f"# Collections ({len(collections)})", ""]
    for c in collections:
        bits = [f"**{c.name}**", f"source: {c.source}"]
        if c.description:
            bits.insert(1, c.description)
        if c.bm25_chunks is not None:
            bits.append(f"bm25: {c.bm25_chunks} chunks")
        if c.vector_count is not None:
            bits.append(f"vectors: {c.vector_count}")
        md_lines.append("- " + " · ".join(bits))
    if not collections:
        md_lines.append(
            "_No collections found. Run `python scripts/ingest.py "
            "--path <pdf> --collection <name>` to create one._",
        )
    structured = {
        "n_collections": len(collections),
        "collections": [
            {
                "name": c.name,
                "source": c.source or "",
                "bm25_chunks": c.bm25_chunks,
                "vector_count": c.vector_count,
                "data_dir": c.data_dir or "",
                "description": c.description,
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
            "Each entry includes its internal collection name, configured business description, and index statistics."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_list_collections,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]