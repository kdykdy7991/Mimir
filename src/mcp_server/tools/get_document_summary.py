"""
``get_document_summary`` — compatibility alias of ``get_document`` (P2.3).

Shares the exact implementation with :func:`get_document`; only the input
schema differs (``doc_id`` is the primary param). The unified not-found
message ``document not found or not accessible`` applies here too.
"""

from __future__ import annotations

from typing import Any

from src.mcp_server.protocol_handler import ProtocolHandler

from src.mcp_server.tools.get_document import (
    OUTPUT_SCHEMA,
    _get_document_item,
)

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


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="get_document_summary",
        description=(
            "Compatibility alias of get_document. Return read-only metadata "
            "for a document by its legacy doc_id."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_get_document_item,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]