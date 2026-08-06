"""
DTOs for query endpoints:

- ``POST /collections/{id}/queries`` — the only way for the Web UI to
  call the RAG retrieval pipeline. Returns a structured response with
  citations, images, and per-stage diagnostics.
- (Trace fetch lives on ``/queries/{query_id}/trace`` — see
  ``traces.py``. The diagnostics here expose the ``trace_id`` so the UI
  can link out to it.)

Per v0.1 contract:
- images are returned as **relative URLs** the Web UI can resolve
  against its API base; never as Base64 inline.
- all scores live in a nested ``scores`` object, each field nullable
  (a chunk may be present in dense but not sparse, etc.).
- degraded reasons is a list because Dense and Rerank can degrade
  independently.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.web_api.schemas._types import ChunkId, UtcDatetime
from src.web_api.schemas.errors import TaskError


QueryMode = Literal["hybrid", "dense", "sparse"]
"""Retrieval mode for the query — matches PRODUCTION spec §10 (hybrid default)."""


class QueryRequest(BaseModel):
    """``POST /collections/{id}/queries`` body."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "公司年假怎么规定?",
                "top_k": 5,
                "mode": "hybrid",
                "enable_rerank": True,
            }
        }
    )

    query: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="User question / search string.",
    )
    top_k: int = Field(
        5,
        ge=1,
        le=50,
        description="Maximum number of chunks to return. Hard-capped to 50.",
    )
    mode: QueryMode = Field(
        "hybrid",
        description=(
            "Retrieval mode: hybrid (default), dense-only, or sparse-only."
        ),
    )
    enable_rerank: bool = Field(
        True,
        description=(
            "If false, the rerank stage is skipped. Equivalent to disabling "
            "the rerank provider in settings but for a single request."
        ),
    )


class CitationScores(BaseModel):
    """Per-stage scores for a single citation.

    Every field is nullable because a chunk may appear in only one
    retrieval branch (e.g. dense-only query) or have no rerank applied.
    """

    dense: float | None = Field(
        None, description="Cosine similarity from the dense retriever (0–1).",
    )
    sparse: float | None = Field(
        None, description="BM25 score from the sparse retriever (raw, not normalised).",
    )
    fusion: float | None = Field(
        None,
        description=(
            "Reciprocal Rank Fusion score — sum of ``1 / (k + rank_i)`` "
            "across the input rankings (default ``k = 60``). **Higher is "
            "better.** Range is roughly ``(0, len(rankings)]``; values "
            "for the top hit are typically small (e.g. ~0.016 for k=60). "
            "See ``src/core/query_engine/fusion.py::reciprocal_rank_fusion``."
        ),
    )
    rerank: float | None = Field(
        None, description="Score from the rerank stage (provider-specific range).",
    )


class CitationImage(BaseModel):
    """Image associated with a citation.

    ``url`` is always a *relative* path (``/api/v1/images/{id}``) so the
    frontend can resolve it against the configured API base URL.
    Local filesystem paths MUST NOT leak into the API.
    """

    id: str = Field(..., description="Stable image identifier.")
    url: str = Field(
        ...,
        description="Relative path, e.g. ``/api/v1/images/abc-123``.",
        examples=["/api/v1/images/abc-123"],
    )
    caption: str | None = Field(
        None, description="Image caption (if multimodal enrichment was enabled).",
    )


class Citation(BaseModel):
    """One retrievable chunk referenced by the answer.

    The 1-based index matches the ``[1]`` markers in the answer text so
    the Web UI can render clickable inline references.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "index": 1,
                "chunk_id": "d2c3v4b5n6m7",
                "document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                "document_name": "employee-handbook.pdf",
                "page": 12,
                "text": "员工年假标准：入职满 1 年不满 10 年，每年 5 天；…",
                "scores": {
                    "dense": 0.81,
                    "sparse": 8.2,
                    "fusion": 0.031,
                    "rerank": 0.91,
                },
                "images": [
                    {
                        "id": "img-001",
                        "url": "/api/v1/images/img-001",
                        "caption": "年假申请流程图",
                    }
                ],
            }
        }
    )

    index: int = Field(..., ge=1, description="1-based position in the citation list.")
    chunk_id: ChunkId
    document_id: UUID
    document_name: str = Field(..., description="Human-readable document name (filename).")
    page: int | None = Field(
        None, ge=1, description="Source page number when applicable.",
    )
    text: str = Field(..., description="Raw chunk text for snippet rendering.")
    scores: CitationScores = Field(default_factory=CitationScores)
    images: list[CitationImage] = Field(default_factory=list)


class QueryDiagnostics(BaseModel):
    """Per-stage counts, latency, and degradation signals.

    Listed in the v0.1 contract — frontend may render these on the
    Playground / Trace page. All counts are nullable so the backend
    can skip reporting a stage it didn't run (e.g. rerank when
    ``enable_rerank=false``).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "duration_ms": 128,
                "trace_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                "dense_count": 20,
                "sparse_count": 20,
                "fused_count": 15,
                "reranked_count": 5,
                "degraded": False,
                "degraded_reasons": [],
            }
        }
    )

    duration_ms: float = Field(..., ge=0, description="End-to-end latency in milliseconds.")
    trace_id: UUID | None = Field(
        None,
        description=(
            "Trace ID for this query; pass to ``GET /queries/{query_id}/trace`` "
            "for the full per-stage breakdown."
        ),
    )
    dense_count: int | None = Field(None, ge=0, description="Chunks returned by the dense retriever.")
    sparse_count: int | None = Field(None, ge=0, description="Chunks returned by the sparse retriever.")
    fused_count: int | None = Field(None, ge=0, description="Chunks after RRF fusion.")
    reranked_count: int | None = Field(None, ge=0, description="Chunks after rerank (0 if skipped).")
    degraded: bool = Field(
        False,
        description="True if at least one stage fell back due to a failure.",
    )
    degraded_reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable reasons; frontend should display but never branch "
            "business logic on these strings. Use ``degraded`` for the boolean."
        ),
    )


class QueryResponse(BaseModel):
    """``POST /collections/{id}/queries`` response.

    ``query_id`` is the query record ID; pass it to
    ``GET /queries/{id}/trace`` for the full Trace JSON.
    ``answer`` is null in v0.1 — final answer generation is out of scope
    (see PRODUCTION §10 "final answer generation as optional independent stage").
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "query_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                    "answer": None,
                    "citations": [
                        {
                            "index": 1,
                            "chunk_id": "d2c3v4b5n6m7",
                            "document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                            "document_name": "employee-handbook.pdf",
                            "page": 12,
                            "text": "员工年假标准：入职满 1 年不满 10 年，每年 5 天；…",
                            "scores": {
                                "dense": 0.81,
                                "sparse": 8.2,
                                "fusion": 0.031,
                                "rerank": 0.91,
                            },
                            "images": [],
                        }
                    ],
                    "diagnostics": {
                        "duration_ms": 128,
                        "trace_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                        "dense_count": 20,
                        "sparse_count": 20,
                        "fused_count": 15,
                        "reranked_count": 5,
                        "degraded": False,
                        "degraded_reasons": [],
                    },
                },
                {
                    "query_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                    "answer": None,
                    "citations": [],
                    "diagnostics": {
                        "duration_ms": 42,
                        "trace_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                        "dense_count": 0,
                        "sparse_count": 0,
                        "fused_count": 0,
                        "reranked_count": 0,
                        "degraded": True,
                        "degraded_reasons": ["no chunks matched the query"],
                    },
                },
                {
                    "query_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                    "answer": None,
                    "citations": [
                        {
                            "index": 1,
                            "chunk_id": "d2c3v4b5n6m7",
                            "document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                            "document_name": "employee-handbook.pdf",
                            "page": 12,
                            "text": "员工年假标准：…",
                            "scores": {
                                "dense": 0.81,
                                "sparse": None,
                                "fusion": 0.031,
                                "rerank": None,
                            },
                            "images": [],
                        }
                    ],
                    "diagnostics": {
                        "duration_ms": 96,
                        "trace_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                        "dense_count": 20,
                        "sparse_count": None,
                        "fused_count": 15,
                        "reranked_count": None,
                        "degraded": True,
                        "degraded_reasons": [
                            "sparse backend unavailable; dense-only fallback",
                            "rerank timeout; using fused ranking",
                        ],
                    },
                },
            ]
        }
    )

    query_id: UUID
    answer: str | None = Field(
        None,
        description=(
            "Final answer text. In v0.1 this is ``null`` — we return chunks "
            "and citations, not generated answers. See PRODUCTION §10."
        ),
    )
    citations: list[Citation] = Field(default_factory=list)
    diagnostics: QueryDiagnostics


class AsyncQueryAccepted(BaseModel):
    """``POST /collections/{id}/queries/async`` response (M3 batch 2).

    Returns immediately with the query id; poll ``GET /queries/{id}/result``
    until it reaches ``succeeded`` / ``failed``. ``task_id`` equals
    ``query_id`` (and the trace id) so one id drives result + trace.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                "task_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                "status": "accepted",
            }
        }
    )

    query_id: UUID
    task_id: UUID
    status: Literal["accepted"] = "accepted"


class AsyncQueryResult(BaseModel):
    """``GET /queries/{query_id}/result`` response (M3 batch 2).

    Poll until ``status`` is ``succeeded`` (``result`` populated) or
    ``failed`` (``error`` populated). ``pending`` / ``running`` mean the
    background query is still in flight.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "succeeded",
                "query_id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                "result": {"query_id": "…", "citations": [], "diagnostics": {}},
                "error": None,
            }
        }
    )

    status: Literal["pending", "running", "succeeded", "failed"]
    query_id: UUID
    result: QueryResponse | None = Field(
        None,
        description="The query response, present only when ``status`` is ``succeeded``.",
    )
    error: TaskError | None = Field(
        None,
        description="Structured error, present only when ``status`` is ``failed``.",
    )


__all__ = [
    "AsyncQueryAccepted",
    "AsyncQueryResult",
    "Citation",
    "CitationImage",
    "CitationScores",
    "QueryDiagnostics",
    "QueryMode",
    "QueryRequest",
    "QueryResponse",
]
