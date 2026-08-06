#!/usr/bin/env python3
"""
Query script — online retrieval via HybridSearch.

Companion to ``scripts/ingest.py``. The typical flow::

    # 1. ingest
    python scripts/ingest.py --path ./docs/report.pdf --data-dir ./data

    # 2. query
    python scripts/query.py --query "BM25 是什么?" --data-dir ./data

Examples
--------
    # Default: top-10 results
    python scripts/query.py --query "如何配置 RAG?"

    # More results
    python scripts/query.py --query "..." --top-k 20

    # Restrict to a specific collection (= BM25 index name)
    python scripts/query.py --query "..." --collection reports

    # See dense / sparse / fusion contributions
    python scripts/query.py --query "..." --verbose

    # Skip the rerank step (D6) when no reranker is configured
    python scripts/query.py --query "..." --no-rerank

Notes
-----
- Output defaults to a compact "Top-K" list. ``--verbose`` adds
  per-stage contributions (dense / sparse / fusion) for debugging.
- The pipeline is built from ``--config`` (default
  ``./config/settings.yaml``) just like ingest.
- D6 Reranker is currently optional — the script constructs a
  reranker if ``settings.rerank.backend != "none"``; otherwise
  the fusion output goes straight to output.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

# Ensure project root is importable when running directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.settings import Settings, load_settings  # noqa: E402
from src.core.query_engine import (  # noqa: E402
    HybridSearch,
    QueryProcessor,
    reciprocal_rank_fusion,
)
from src.core.query_engine.dense_retriever import DenseRetriever  # noqa: E402
from src.core.query_engine.sparse_retriever import SparseRetriever  # noqa: E402
from src.core.types import RetrievalResult  # noqa: E402
from src.ingestion.embedding.sparse_encoder import SparseEncoder  # noqa: E402
from src.ingestion.storage import BM25Indexer  # noqa: E402

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext

logger = logging.getLogger("query")


# ---------------------------------------------------------------------------
# Pipeline assembly
# ---------------------------------------------------------------------------

def build_query_components(
    *,
    data_dir: str,
    collection: str = "default",
    embedding: Any,
    vector_store: Any,
    sparse_encoder: Any | None = None,
    reranker: Any | None = None,
) -> HybridSearch:
    """
    Wire the D1+D2+D3+D4 modules into a :class:`HybridSearch`.

    All collaborators except ``data_dir`` / ``collection`` are
    injected so tests can swap in fake backends (the script's
    CLI uses real factories).
    """
    db = Path(data_dir) / "db"
    bm25_dir = db / "bm25"

    sparse_encoder = sparse_encoder or SparseEncoder()
    bm25 = BM25Indexer(
        persist_dir=str(bm25_dir),
        sparse_encoder=sparse_encoder,
    )
    processor = QueryProcessor(
        sparse_encoder=sparse_encoder,
    )
    dense = DenseRetriever(
        embedding=embedding, vector_store=vector_store,
    )
    sparse = SparseRetriever(
        bm25_indexer=bm25,
        vector_store=vector_store,
        index_name=collection,
    )
    return HybridSearch(
        query_processor=processor,
        dense_retriever=dense,
        sparse_retriever=sparse,
        fusion=reciprocal_rank_fusion,
    )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _truncate(text: str, max_len: int = 200) -> str:
    """Compact a chunk's text for display."""
    if not text:
        return "(no text)"
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _source_line(result: RetrievalResult) -> str:
    """Format the ``source_file / page`` line for one result."""
    meta = result.metadata or {}
    parts: list[str] = []
    src = meta.get("source_path") or meta.get("source")
    if src:
        parts.append(str(src))
    page = meta.get("page_num") or meta.get("page")
    if page is not None:
        parts.append(f"page {page}")
    return " · ".join(parts) if parts else "(no source)"


def _leg_recall(trace: "TraceContext | None") -> tuple[int, int]:
    """
    Read per-leg recall counts ``(dense, sparse)`` off a search trace.

    Counts come from each retriever's own ``finish`` event (``n_out``)
    rather than the ``fusion`` stage, because fusion only records
    ``n_dense`` / ``n_sparse`` when *both* legs returned something —
    its single-leg passthrough branches carry just one of the two, and
    the ``_timed()`` helper that records the both-legs case emits no
    ``event`` key at all.

    A missing leg (retriever failed, or trace is None because
    ``--verbose`` was off) reports 0 for that leg.
    """
    if trace is None:
        return 0, 0
    dense = sparse = 0
    for stage in trace.stages:
        if stage.get("event") != "finish":
            continue
        if stage.get("name") == DenseRetriever.name:
            dense = stage.get("n_out", 0)
        elif stage.get("name") == SparseRetriever.name:
            sparse = stage.get("n_out", 0)
    return dense, sparse


def format_results(
    results: list[RetrievalResult],
    *,
    verbose: bool = False,
    dense_count: int = 0,
    sparse_count: int = 0,
) -> str:
    """Render the final result list for stdout."""
    if not results:
        return (
            "未找到相关文档。请确认已运行 ingest.py 摄取数据,"
            "或尝试调整 query / top_k。"
        )

    lines: list[str] = []
    if verbose:
        lines.append(
            f"[query] verbose: dense_recall={dense_count} "
            f"sparse_recall={sparse_count} fused={len(results)}"
        )
    for i, r in enumerate(results, start=1):
        score_str = f"{r.score:.4f}" if r.score is not None else "n/a"
        lines.append(f"  {i}. [{score_str}] {r.chunk_id}  ({r.source})")
        lines.append(f"     src: {_source_line(r)}")
        lines.append(f"     {_truncate(r.text)}")
        lines.append("")  # blank line between results
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="query",
        description=(
            "Run a hybrid search (dense + sparse + RRF fusion) "
            "against the ingested knowledge hub and print the "
            "top-k results."
        ),
    )
    parser.add_argument(
        "--query", required=True,
        help="The natural-language query.",
    )
    parser.add_argument(
        "--top-k", type=int, default=10,
        help="Maximum number of results to return (default: 10).",
    )
    parser.add_argument(
        "--collection", default="default",
        help=(
            "Collection name (also used as the BM25 index name). "
            "Default: 'default'."
        ),
    )
    parser.add_argument(
        "--data-dir", default="./data",
        help="Root data directory (default: ./data). The script "
             "reads <data-dir>/db/bm25/<collection>.json and the "
             "vector store configured by --config.",
    )
    parser.add_argument(
        "--config", default="./config/settings.yaml",
        help="Path to settings.yaml (default: ./config/settings.yaml).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show dense / sparse / fusion intermediate counts.",
    )
    parser.add_argument(
        "--no-rerank", action="store_true",
        help="Skip the (optional) rerank step. Useful when no "
             "reranker is configured.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    # Load settings.
    config_path = Path(args.config)
    if config_path.is_file():
        settings = load_settings(str(config_path))
    else:
        logger.warning(
            "Settings file %s not found — using defaults.", config_path,
        )
        settings = Settings()

    # Defer heavy backend imports until after arg parsing.
    from src.libs.embedding import EmbeddingFactory
    from src.libs.vector_store import VectorStoreFactory

    embedding = EmbeddingFactory.create(settings.embedding)
    vector_store = VectorStoreFactory.create(settings.vector_store)
    sparse_encoder = SparseEncoder.from_settings(settings.sparse)

    # Build the hybrid search pipeline, wrapped in the application
    # service so the CLI shares the same stable dependency as MCP /
    # Streamlit / Web API (M1: thin facade over HybridSearch).
    from src.application.services import QueryService

    hybrid = build_query_components(
        data_dir=args.data_dir,
        collection=args.collection,
        embedding=embedding,
        vector_store=vector_store,
        sparse_encoder=sparse_encoder,
    )
    query_service = QueryService(hybrid)

    # A trace is created up-front when --verbose is set, so the single
    # search call below yields both the results we print and the stage
    # counts we report on. (Re-running the search just to collect a
    # trace would double every embedding API call, and let the printed
    # counts disagree with the printed results.)
    trace = None
    if args.verbose:
        from src.core.trace.trace_context import new_trace
        trace = new_trace()

    # Run the search. D6 Reranker is optional; if --no-rerank is
    # set, or the configured backend is "none", we skip it.
    rerank_used = False
    fallback = False
    if not args.no_rerank and settings.rerank.backend != "none":
        from src.libs.reranker import RerankerFactory
        from src.core.query_engine.reranker import RerankerStage
        reranker = RerankerFactory.create(settings.rerank)
        # top_m cap: how many candidates to send to the (potentially
        # expensive) reranker. Use settings.rerank.top_m.
        rerank_stage = RerankerStage(
            reranker=reranker, top_m=settings.rerank.top_m,
        )
        # RerankerStage swallows backend errors and returns
        # RerankOutput with fallback=True — no need for an outer
        # try/except here.
        output = rerank_stage.rerank(
            args.query,
            query_service.search(args.query, top_k=args.top_k, trace=trace).chunks,
            trace=trace,
        )
        results = output.results
        rerank_used = True
        fallback = output.fallback
        if fallback:
            logger.warning(
                "Reranker fallback engaged (%s) — using fusion output.",
                output.error,
            )
    else:
        results = query_service.search(
            args.query, top_k=args.top_k, trace=trace,
        ).chunks

    dense_count, sparse_count = _leg_recall(trace)

    # Render output.
    output = format_results(
        results,
        verbose=args.verbose,
        dense_count=dense_count,
        sparse_count=sparse_count,
    )
    print(output)
    if args.verbose:
        flag = (
            "on" if rerank_used and not fallback
            else "fallback" if fallback
            else "off"
        )
        print(
            f"[query] rerank={flag} "
            f"top_k={args.top_k} collection={args.collection}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
