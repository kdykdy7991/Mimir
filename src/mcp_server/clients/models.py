"""
Read-only domain models for the MCP client boundary (P1.1).

These are the only shapes that cross the ``RagReadOnlyClient``
interface. They are deliberately pure — no ``mcp`` content types,
no Chroma records, no Web DTOs, no LLM types — so the MCP tool
handlers, the in-process client and the HTTP client all speak one
stable vocabulary (see plan §6 / §7 and P1.1).

Compatibility fields (``n_results``/``citations``) are exposed as
derived properties on :class:`KnowledgeQueryResult` so callers can
keep producing the legacy structured payload while the canonical
``count``/``evidence`` stay authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CollectionInfo:
    """One authorized knowledge base discoverable via ``list_collections``."""

    name: str
    description: str | None = None
    # Stats may be unavailable — never fabricate zero for an unknown count.
    document_count: int | None = None
    chunk_count: int | None = None


@dataclass(frozen=True)
class QueryRequest:
    """A single evidence-retrieval request (no answer generation)."""

    query: str
    collection: str
    top_k: int = 10
    rerank: bool = True


@dataclass(frozen=True)
class EvidenceItem:
    """One retrieved evidence chunk for :class:`KnowledgeQueryResult`."""

    rank: int
    chunk_id: str
    document_id: str
    title: str = ""
    source: str = ""
    page: int | None = None
    score: float = 0.0
    text: str = ""


@dataclass(frozen=True)
class Diagnostics:
    """Degradation + tracing info for a query (never silently hide a
    partial pipeline as a full one)."""

    degraded: bool = False
    reasons: list[str] = field(default_factory=list)
    trace_id: str | None = None


@dataclass(frozen=True)
class KnowledgeQueryResult:
    """Evidence result — no answer, no LLM output."""

    query: str
    collection: str
    count: int
    evidence: list[EvidenceItem] = field(default_factory=list)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)

    # -- Compatibility (legacy callers) ----------------------------------
    @property
    def n_results(self) -> int:
        """Legacy alias for ``count`` (kept one release cycle)."""
        return self.count

    @property
    def citations(self) -> list[dict[str, object]]:
        """Legacy ``citations`` list, mirroring ``evidence`` after the
        pre-P2.2 citation shape (index/chunk_id/source/page/score/
        source_type/text_excerpt)."""
        return [
            {
                "index": item.rank,
                "chunk_id": item.chunk_id,
                "source": item.source,
                "page": item.page,
                "score": item.score,
                "source_type": "retrieval",
                "text_excerpt": item.text,
            }
            for item in self.evidence
        ]


@dataclass(frozen=True)
class DocumentInfo:
    """Read-only document detail (metadata only; no chunk body)."""

    document_id: str
    collection: str
    title: str = ""
    document_type: str = ""
    source: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    chunk_count: int = 0


@dataclass(frozen=True)
class DocumentChunk:
    """One stored document chunk (stable id + ordering) for pagination."""

    chunk_id: str
    index: int
    text: str = ""
    page: int | None = None
    section: str = ""


@dataclass(frozen=True)
class DocumentChunkPage:
    """A page of a document's chunks, ordered by stable position."""

    document_id: str
    page: int
    page_size: int
    total: int
    chunks: list[DocumentChunk] = field(default_factory=list)

    @property
    def has_next(self) -> bool:
        return (self.page * self.page_size) < self.total


__all__ = [
    "CollectionInfo",
    "QueryRequest",
    "EvidenceItem",
    "Diagnostics",
    "KnowledgeQueryResult",
    "DocumentInfo",
    "DocumentChunk",
    "DocumentChunkPage",
]