"""
E3: ``query_knowledge_hub`` tool (P2.2 normalised contract).

Returns *retrieval evidence* from an authorized knowledge base — never an
answer. The handler is thin: validate input → resolve+authorize the
collection → ask the :class:`RagReadOnlyClient` → format the evidence.

P2.2 changes (§5.2):

- ``no_rerank`` is a deprecated alias; the canonical ``rerank`` boolean
  takes precedence when both are given.
- ``query`` length is validated (1..2000).
- Structured output carries ``count`` / ``evidence`` / ``diagnostics`` /
  ``collection``; the legacy ``n_results`` / ``citations`` remain for the
  compatibility window.
- Tool / description wording no longer invites answer generation.
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

MAX_QUERY_LENGTH = 2000

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_QUERY_LENGTH,
            "description": (
                "The text to retrieve evidence for. Up to "
                f"{MAX_QUERY_LENGTH} characters."
            ),
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 10,
            "description": "Maximum number of evidence results to return.",
        },
        "collection": {
            "type": "string",
            "default": "default",
            "description": (
                "Collection name (= BM25 index name) to query."
            ),
        },
        "rerank": {
            "type": "boolean",
            "default": True,
            "description": (
                "Whether to apply the optional rerank stage when one is "
                "configured. Highly recommended for precision."
            ),
        },
        "no_rerank": {
            "type": "boolean",
            "default": False,
            "description": (
                "Deprecated alias for `rerank`. Use `rerank` instead."
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
        "collection": {"type": "string"},
        "count": {"type": "integer"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer"},
                    "chunk_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "title": {"type": "string"},
                    "source": {"type": "string"},
                    "page": {"type": ["integer", "null"]},
                    "score": {"type": "number"},
                    "text": {"type": "string"},
                },
                "required": ["rank", "chunk_id", "document_id", "source", "text"],
            },
        },
        "diagnostics": {
            "type": "object",
            "properties": {
                "degraded": {"type": "boolean"},
                "reasons": {"type": "array", "items": {"type": "string"}},
                "trace_id": {"type": ["string", "null"]},
            },
            "required": ["degraded", "reasons"],
        },
        "n_results": {
            "type": "integer",
            "description": "Deprecated alias for `count`.",
        },
        "citations": {
            "type": "array",
            "description": "Deprecated alias for `evidence`.",
            "items": {"type": "object"},
        },
    },
    "required": ["query", "collection", "count", "evidence", "diagnostics"],
}

_EMPTY_HINT = (
    "未找到相关文档。请确认已运行 ingest.py 完成数据入库，"
    "或尝试调整 query / top_k。"
)


def _resolve_rerank(args: dict[str, Any]) -> bool:
    no_rerank = args.get("no_rerank")
    if "rerank" in args:
        return bool(args.get("rerank"))
    return not no_rerank


def _excerpt(text: str, limit: int = 200) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format(result) -> tuple[str, dict[str, Any]]:
    rows = []
    for item in result.evidence:
        header = f"**[{item.rank}] {item.source}**"
        if item.page is not None:
            header += f" (page {item.page})"
        rows.append((header, _excerpt(item.text)))
    if not rows:
        return _EMPTY_HINT, {
            "query": result.query,
            "collection": result.collection,
            "count": 0,
            "evidence": [],
            "diagnostics": {
                "degraded": result.diagnostics.degraded,
                "reasons": list(result.diagnostics.reasons),
                "trace_id": result.diagnostics.trace_id,
            },
            "n_results": 0,
            "citations": [],
        }

    body = "\n\n---\n\n".join(f"{h}\n\n{t}" for h, t in rows)
    refs = ["", "## References", ""]
    for item in result.evidence:
        meta = f"p.{item.page}" if item.page is not None else "n/a"
        refs.append(
            f"[{item.rank}] `{item.chunk_id}` — {item.source} "
            f"({meta}, score={item.score:.4f})",
        )
    markdown = body + "\n".join(refs)
    structured = {
        "query": result.query,
        "collection": result.collection,
        "count": result.count,
        "evidence": [
            {
                "rank": item.rank,
                "chunk_id": item.chunk_id,
                "document_id": item.document_id,
                "title": item.title,
                "source": item.source,
                "page": item.page,
                "score": item.score,
                "text": item.text,
            }
            for item in result.evidence
        ],
        "diagnostics": {
            "degraded": result.diagnostics.degraded,
            "reasons": list(result.diagnostics.reasons),
            "trace_id": result.diagnostics.trace_id,
        },
        # Compatibility window.
        "n_results": result.n_results,
        "citations": result.citations,
    }
    return markdown, structured


async def _query_knowledge_hub(args: dict[str, Any]) -> Any:
    query: str = (args.get("query") or "").strip()
    if not query:
        return tool_error(
            "'query' is required and must be a non-empty string",
        )
    if len(query) > MAX_QUERY_LENGTH:
        return tool_error(
            f"'query' exceeds the {MAX_QUERY_LENGTH}-character limit",
        )
    top_k: int = int(args.get("top_k") or 10)
    try:
        collection = resolve_query_collection(
            current_principal(), args.get("collection"),
        )
    except (CollectionAccessDenied, CollectionSelectionRequired) as exc:
        return tool_error(str(exc))

    client = client_from_args(args)
    result = client.query_knowledge(
        QueryRequest(
            query=query,
            collection=collection,
            top_k=top_k,
            rerank=_resolve_rerank(args),
        ),
        current_principal(),
    )
    return _format(result)


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="query_knowledge_hub",
        description=(
            "Search an authorized SKDY knowledge base and return ranked "
            "retrieval evidence (chunks + citations). Returns NO answer — "
            "the evidence is for your own synthesis. Use list_collections "
            "first to identify the collection name."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_query_knowledge_hub,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]