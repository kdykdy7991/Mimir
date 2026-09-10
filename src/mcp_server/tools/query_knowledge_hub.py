"""
E3: ``query_knowledge_hub`` tool.

Returns *raw retrieval evidence* from an authorized knowledge base. The
handler is thin: validate input → resolve+authorize the collection →
ask the :class:`RagReadOnlyClient` → format the evidence result into
Markdown + structured content. It never constructs an Embedding / Vector
Store / SQLite / Reranker stack itself (P1.2).

Phase-1 note: the output keeps the legacy citation shape and wording so
the migration matches the Phase-0 baseline; P2.2 introduces the
evidence-centric contract (``evidence`` / ``diagnostics`` / ``rerank``)
and drops the "ask/answer" wording.
"""

from __future__ import annotations

from typing import Any

from src.mcp_server.auth.authorization import (
    CollectionAccessDenied,
    CollectionSelectionRequired,
    resolve_query_collection,
)
from src.mcp_server.auth.context import current_principal
from src.mcp_server.clients.models import QueryRequest
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error

from src.mcp_server.tools.common import client_from_args

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "The natural-language question to ask the knowledge hub."
            ),
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 10,
            "description": "Maximum number of results to return.",
        },
        "collection": {
            "type": "string",
            "default": "default",
            "description": (
                "Collection name (= BM25 index name) to query."
            ),
        },
        "no_rerank": {
            "type": "boolean",
            "default": False,
            "description": (
                "Skip the (optional) rerank stage even if a reranker "
                "is configured. Useful for latency-sensitive calls."
            ),
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "n_results": {"type": "integer"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "chunk_id": {"type": "string"},
                    "source": {"type": "string"},
                    "page": {"type": ["integer", "null"]},
                    "score": {"type": "number"},
                    "source_type": {"type": "string"},
                    "text_excerpt": {"type": "string"},
                },
                "required": [
                    "index", "chunk_id", "source", "score",
                    "source_type", "text_excerpt",
                ],
            },
        },
    },
    "required": ["query", "n_results", "citations"],
}

_EMPTY_HINT = (
    "未找到相关文档。请确认已运行 ingest.py 完成数据入库，"
    "或尝试调整 query / top_k。"
)


def _excerpt(text: str, limit: int = 200) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format(result) -> tuple[str, dict[str, Any]]:
    """Render a KnowledgeQueryResult into legacy markdown + structured."""
    rows = []
    for item in result.evidence:
        header = f"**[{item.rank}] {item.source}**"
        if item.page is not None:
            header += f" (page {item.page})"
        rows.append((header, _excerpt(item.text)))
    if not rows:
        return _EMPTY_HINT, {
            "query": result.query,
            "n_results": 0,
            "citations": [],
        }

    body = "\n\n---\n\n".join(f"{h}\n\n{t}" for h, t in rows)
    refs = ["", "## References", ""]
    for item in result.evidence:
        meta = f"p.{item.page}" if item.page is not None else "n/a"
        refs.append(
            f"[{item.rank}] `{item.chunk_id}` — {item.source} "
            f"({meta}, score={item.score:.4f}, via {item.source_type})",
        )
    markdown = body + "\n".join(refs)
    structured = {
        "query": result.query,
        "n_results": result.n_results,
        "citations": [
            {
                "index": item.rank,
                "chunk_id": item.chunk_id,
                "source": item.source,
                "page": item.page,
                "score": item.score,
                "source_type": item.source_type,
                "text_excerpt": _excerpt(item.text),
            }
            for item in result.evidence
        ],
    }
    return markdown, structured


async def _query_knowledge_hub(args: dict[str, Any]) -> Any:
    query: str = (args.get("query") or "").strip()
    if not query:
        return tool_error(
            "'query' is required and must be a non-empty string",
        )
    top_k: int = int(args.get("top_k") or 10)
    try:
        collection = resolve_query_collection(
            current_principal(), args.get("collection"),
        )
    except (CollectionAccessDenied, CollectionSelectionRequired) as exc:
        return tool_error(str(exc))
    no_rerank: bool = bool(args.get("no_rerank") or False)

    client = client_from_args(args)
    result = client.query_knowledge(
        QueryRequest(
            query=query,
            collection=collection,
            top_k=top_k,
            rerank=not no_rerank,
        ),
        current_principal(),
    )
    return _format(result)


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="query_knowledge_hub",
        description=(
            "Search an authorized SKDY knowledge base for evidence relevant to a question. "
            "Use list_collections first to identify the collection name. Returns cited results."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_query_knowledge_hub,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]