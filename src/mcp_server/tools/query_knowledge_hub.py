"""
E3: ``query_knowledge_hub`` tool.

Runs a RAG query against a named collection using the D5
``HybridSearch`` orchestrator and the D6 ``RerankerStage`` (when
configured), then formats the results through the response builder
into a Markdown-with-citations output.

The wiring reuses the same ``build_query_components`` helper that
``scripts/query.py`` uses, so behaviour stays consistent between
the CLI and the MCP surface.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from src.core.query_engine.reranker import RerankerStage
from src.core.response import build_response
from src.core.response.multimodal_assembler import (
    ImageNotFoundError,
    MultimodalAssembler,
)
from src.application.services import QueryService
from src.core.settings import Settings, load_settings
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage.image_storage import ImageStorage
from src.libs.embedding import EmbeddingFactory
from src.libs.reranker import RerankerFactory
from src.libs.vector_store import VectorStoreFactory
from src.libs.vector_store.scoped import ScopedCollectionVectorStore
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error
from src.mcp_server.auth.authorization import (
    CollectionAccessDenied, CollectionSelectionRequired, resolve_query_collection,
)
from src.mcp_server.auth.context import current_principal

# ``scripts/`` lives at the project root. When the MCP server is launched
# by a client (Claude Desktop / Cursor / Copilot) the working directory
# may not be the project root, so we explicitly add it to sys.path before
# importing from ``scripts/``.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
if _PROJECT_ROOT_STR not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_STR)

from scripts.query import build_query_components  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON Schema for the tool's input
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Settings + pipeline
# ---------------------------------------------------------------------------

def _load_settings(config_path: str) -> Settings:
    """Reload settings on every call to honour config edits."""
    p = Path(config_path)
    if p.is_file():
        return load_settings(str(p))
    return Settings()


def _build_search(
    *,
    config_path: str,
    collection: str,
    data_dir: str,
    no_rerank: bool,
):
    """
    Build the full query stack: ``QueryService`` (thin facade over
    ``HybridSearch``) + (optional) ``RerankerStage``.
    Returns ``(query_service, rerank_stage_or_None)``.
    """
    settings = _load_settings(config_path)

    embedding = EmbeddingFactory.create(settings.embedding)
    # M3 multi-collection: the real data lives in per-collection Chroma
    # stores, so a single-collection store bound to the settings' default
    # would query an empty collection (dense) and drop every sparse hit at
    # the get_by_ids reverse-lookup. Route via the multi-collection router
    # and scope it to the requested collection — same pattern as
    # ``EngineCache._scoped_store`` (src/application/engines.py).
    router = VectorStoreFactory.create_multi_collection(settings.vector_store)
    vector_store = ScopedCollectionVectorStore(router, collection)
    sparse_encoder = SparseEncoder.from_settings(settings.sparse)

    hybrid = build_query_components(
        data_dir=data_dir,
        collection=collection,
        embedding=embedding,
        vector_store=vector_store,
        sparse_encoder=sparse_encoder,
    )
    # M1: route the MCP tool through the application service so all four
    # entry points share the same stable dependency.
    query_service = QueryService(hybrid)

    rerank_stage: RerankerStage | None = None
    if not no_rerank and settings.rerank.backend != "none":
        try:
            reranker = RerankerFactory.create(settings.rerank)
            rerank_stage = RerankerStage(
                reranker=reranker,
                top_m=settings.rerank.top_m,
            )
        except Exception as exc:  # noqa: BLE001
            # Don't fail the call — degrade to fusion-only.
            logger.warning(
                "reranker unavailable, falling back to fusion: %s", exc,
            )
            rerank_stage = None

    return query_service, rerank_stage


# ---------------------------------------------------------------------------
# Tool entry
# ---------------------------------------------------------------------------

async def _query_knowledge_hub(
    args: dict[str, Any],
) -> Any:
    query: str = (args.get("query") or "").strip()
    if not query:
        # Known parameter error → CallToolResult(is_error=True), not a
        # protocol MCPError (see protocol_handler "Error mapping").
        return tool_error(
            "'query' is required and must be a non-empty string",
        )
    top_k: int = int(args.get("top_k") or 10)
    try:
        collection = resolve_query_collection(current_principal(), args.get("collection"))
    except (CollectionAccessDenied, CollectionSelectionRequired) as exc:
        return tool_error(str(exc))
    no_rerank: bool = bool(args.get("no_rerank") or False)

    # Internal hints the server injects from CLI flags. Tools that
    # are exposed to clients only see the public schema.
    config_path = args.get("_config_path") or "./config/settings.yaml"
    data_dir = args.get("_data_dir") or "./data"

    query_service, rerank_stage = _build_search(
        config_path=config_path,
        collection=collection,
        data_dir=data_dir,
        no_rerank=no_rerank,
    )

    candidates = query_service.search(query, top_k=top_k).chunks

    if rerank_stage is not None and candidates:
        output = rerank_stage.rerank(query, candidates)
        results = output.results
    else:
        results = candidates

    # E6: assemble multimodal content blocks (text + base64 image)
    # so clients that understand MCP image content can render
    # inline images. Falls back to text-only on missing-image
    # errors so a stale image_id never breaks a successful
    # retrieval.
    assembler = _build_assembler(data_dir=data_dir)
    try:
        response = build_response(results, query, assembler=assembler)
        return response.as_content_pair()
    except (ImageNotFoundError, OSError) as exc:
        # ImageNotFoundError = DB has no row for the id; OSError = the DB
        # row exists but the underlying file is gone (stale reference).
        # Either way the retrieval itself succeeded — degrade to text.
        logger.warning(
            "image for %r missing; falling back to text-only "
            "response: %s",
            getattr(exc, "image_id", None), exc,
        )
        response = build_response(results, query)
        return response.as_pair()


def _build_assembler(*, data_dir: str) -> MultimodalAssembler:
    """
    Build a :class:`MultimodalAssembler` backed by the default
    :class:`ImageStorage` rooted at ``data_dir``.

    ImageStorage stores its DB at ``data_dir/db/image_index.db``
    and image files under ``data_dir/images/`` — the same
    layout produced by ``IngestionPipeline`` (C14).
    """
    storage = ImageStorage(
        db_path=str(Path(data_dir) / "db" / "image_index.db"),
        base_dir=str(Path(data_dir) / "images"),
    )
    return MultimodalAssembler(image_storage=storage)


def register(handler: ProtocolHandler) -> None:
    """Register this tool on the given protocol handler."""
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
