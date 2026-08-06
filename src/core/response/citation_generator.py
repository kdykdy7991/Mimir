"""
Citation generator — produces structured citations from retrieval results.

Each citation captures the minimum information an MCP client needs to
let the user drill into a source: where the chunk came from, which
page, and the chunk id + score that landed it in the result list.

The generator is intentionally tiny — it does no LLM calls, just
shape-mapping. LLM-generated "explanations" of why a citation is
relevant belong elsewhere (the response builder's job).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.core.types import RetrievalResult


@dataclass
class Citation:
    """A single retrievable source in a query response."""
    index: int              # 1-based position in the response
    chunk_id: str
    source: str             # file path or "no source"
    page: int | None
    score: float
    source_type: str        # "dense" | "sparse" | "fusion" | "rerank"
    text_excerpt: str       # first ~200 chars

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source_path(r: RetrievalResult) -> str:
    meta = r.metadata or {}
    return str(
        meta.get("source_path")
        or meta.get("source")
        or "(no source)",
    )


def _page(r: RetrievalResult) -> int | None:
    meta = r.metadata or {}
    page = meta.get("page_num")
    if page is None:
        page = meta.get("page")
    try:
        return int(page) if page is not None else None
    except (TypeError, ValueError):
        return None


def _excerpt(text: str, limit: int = 200) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def generate(results: list[RetrievalResult]) -> list[Citation]:
    """
    Build a citation list from retrieval results, in input order.
    The ``index`` is 1-based so it can be used directly in Markdown
    references like ``[1]``, ``[2]``.
    """
    citations: list[Citation] = []
    for i, r in enumerate(results, start=1):
        citations.append(Citation(
            index=i,
            chunk_id=r.chunk_id,
            source=_source_path(r),
            page=_page(r),
            score=float(r.score) if r.score is not None else 0.0,
            source_type=r.source or "fusion",
            text_excerpt=_excerpt(r.text),
        ))
    return citations


__all__ = ["Citation", "generate"]
