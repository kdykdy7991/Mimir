"""
E5: ``get_document_summary`` tool.

Given a ``doc_id`` (the stable UUID exposed by the Web API — derived
from ``(collection, source_path)`` via ``document_uuid``), return a
summary of the document: title, summary, tags, source path, and the
number of chunks indexed.

Resolution is cross-collection: the UUID is mapped back to its
``(collection, source_path)`` via the ingestion-history registry
(same lookup the Web API's ``mappers.resolve_document`` performs),
then the chunks are fetched from the right per-collection Chroma
store by the ``source_path`` metadata filter. This works for any
collection, not just the configured default.

If the document is not found, the tool returns a clear
``is_error=True`` result so the caller (MCP client) gets a useful
message rather than a stack trace.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from src.core.settings import Settings, load_settings
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error
from src.mcp_server.auth.authorization import CollectionAccessDenied, require_collection_access
from src.mcp_server.auth.context import current_principal

logger = logging.getLogger(__name__)


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


def _resolve_doc(
    integrity_db: str, doc_id: UUID | str,
) -> tuple[str, str] | None:
    """
    Map ``doc_id`` (a stable UUID) back to ``(collection, source_path)``.

    Document ids are ``uuid5(namespace, f"document:{collection}:{path}")``
    (see ``src/application/identifiers``) — the same derivation the Web
    API's ``mappers.resolve_document`` enumerates against. The ingestion
    history SQLite DB is the authoritative document registry, so a doc
    whose UUID was issued by any collection resolves here. Non-UUID
    strings simply never match and fall through to ``None``.
    """
    from src.application.identifiers import document_uuid
    from src.libs.loader.file_integrity import SQLiteIntegrityChecker

    checker = SQLiteIntegrityChecker(db_path=integrity_db)
    try:
        for rec in checker.list_processed(status="success"):
            if str(document_uuid(rec.collection, rec.file_path)) == str(doc_id):
                return rec.collection, rec.file_path
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ingestion history read failed for %s: %s", doc_id, exc,
        )
    return None


async def _get_document_summary(args: dict[str, Any]) -> Any:
    doc_id = (args.get("doc_id") or "").strip()
    if not doc_id:
        # Known parameter error → CallToolResult(is_error=True), not a
        # protocol MCPError (see protocol_handler "Error mapping").
        return tool_error(
            "'doc_id' is required and must be a non-empty string",
        )

    config_path = args.get("_config_path") or "./config/settings.yaml"
    data_dir = args.get("_data_dir") or "./data"
    p = Path(config_path)
    settings = load_settings(str(p)) if p.is_file() else Settings()

    resolved = _resolve_doc(
        str(Path(data_dir) / "db" / "ingestion_history.db"), doc_id,
    )
    if resolved is None:
        # Business error (document doesn't exist) → is_error result.
        return tool_error(
            f"document not found: doc_id={doc_id!r}. "
            f"Confirm the id is correct and the document has been ingested.",
        )
    collection, source_path = resolved
    try:
        require_collection_access(current_principal(), collection)
    except CollectionAccessDenied:
        # Do not let a valid-but-forbidden UUID probe another collection.
        return tool_error("document not found or not accessible")

    # Scope the multi-collection router to the document's own collection
    # (data lives in per-collection Chroma stores, not the configured
    # default) and fetch its chunks by the source_path metadata filter —
    # the same key ``DocumentManager.get_document_detail`` uses.
    from src.libs.vector_store import VectorStoreFactory
    from src.libs.vector_store.scoped import ScopedCollectionVectorStore

    try:
        router = VectorStoreFactory.create_multi_collection(settings.vector_store)
        store = ScopedCollectionVectorStore(router, collection)
        hits = store.get_by_metadata(
            {"source_path": source_path}, limit=10_000,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("vector store lookup failed for %s: %s", doc_id, exc)
        hits = []

    if not hits:
        # Registry says it ingested but the vector store is empty —
        # surface it rather than pretending it doesn't exist.
        return tool_error(
            f"document not found: doc_id={doc_id!r}. "
            f"Confirm the id is correct and the document has been ingested.",
        )

    first = hits[0]
    meta = first.get("metadata") or {}

    structured = {
        "doc_id": doc_id,
        "title": str(meta.get("title") or "(untitled)"),
        "summary": str(meta.get("summary") or ""),
        "tags": list(meta.get("tags") or []),
        "source_path": str(
            meta.get("source_path") or source_path or "",
        ),
        "doc_type": str(meta.get("doc_type") or ""),
        "chunk_count": int(len(hits)),
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
    markdown = "\n".join(md_lines)
    return markdown, structured


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="get_document_summary",
        description=(
            "Return the title, summary, tags, and chunk count for a "
            "document by its doc_id (the stable UUID from the Web API)."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_get_document_summary,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]
