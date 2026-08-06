"""
DTOs for trace endpoints:

- ``GET /queries/{id}/trace``
- ``GET /ingestions/{id}/trace``

Both return the same shape so the Web UI can render the trace timeline
with one component. Trace data comes from the existing JSONL logs
(``src/observability/``) — see PRODUCTION §3.4 for the underlying format.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.web_api.schemas._types import UtcDatetime


TraceType = Literal["query", "ingestion"]
"""Discriminator for the two trace shapes we currently emit.

Exposed as a top-level Literal so generated TypeScript types produce a
narrow string union rather than ``string``. New values (e.g.
``"evaluation"``) only get added when the backend actually emits them —
see v0.1 contract §6.
"""


class TraceStage(BaseModel):
    """One stage inside a trace.

    ``method`` / ``provider`` are the dynamic-component hints the
    Dashboard already uses to render provider-specific detail
    (see DEV_SPEC §3.4.4 / §3.4.5).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "rerank",
                "method": "cross_encoder",
                "provider": "cross-encoder/ms-marco-MiniLM-L-6-v2",
                "started_at": "2026-07-31T08:23:11.350Z",
                "duration_ms": 23.4,
                "details": {"input_count": 15, "output_count": 5},
            }
        }
    )

    name: str = Field(..., description="Stage name, e.g. 'dense', 'sparse', 'rerank'.")
    method: str | None = Field(None, description="Implementation method, e.g. 'bm25', 'rrf'.")
    provider: str | None = Field(None, description="Provider name, e.g. 'openai', 'cross_encoder'.")
    started_at: UtcDatetime
    duration_ms: float = Field(..., ge=0, description="Stage duration in milliseconds.")
    details: dict = Field(
        default_factory=dict,
        description="Provider-specific or method-specific extra fields.",
    )


class TraceResponse(BaseModel):
    """``GET /queries/{id}/trace`` and ``GET /ingestions/{id}/trace`` response."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                "trace_type": "query",
                "started_at": "2026-07-31T08:23:11.234Z",
                "finished_at": "2026-07-31T08:23:11.362Z",
                "total_latency_ms": 128.0,
                "stages": [
                    {
                        "name": "dense",
                        "method": "cosine",
                        "provider": "openai",
                        "started_at": "2026-07-31T08:23:11.250Z",
                        "duration_ms": 60.0,
                        "details": {"top_k": 20},
                    },
                    {
                        "name": "sparse",
                        "method": "bm25",
                        "provider": "bm25",
                        "started_at": "2026-07-31T08:23:11.250Z",
                        "duration_ms": 12.0,
                        "details": {"top_k": 20},
                    },
                    {
                        "name": "fusion",
                        "method": "rrf",
                        "provider": "rrf",
                        "started_at": "2026-07-31T08:23:11.315Z",
                        "duration_ms": 2.0,
                        "details": {"k": 60},
                    },
                    {
                        "name": "rerank",
                        "method": "cross_encoder",
                        "provider": "cross-encoder/ms-marco-MiniLM-L-6-v2",
                        "started_at": "2026-07-31T08:23:11.350Z",
                        "duration_ms": 23.4,
                        "details": {"input_count": 15, "output_count": 5},
                    },
                ],
                "error": None,
            }
        }
    )

    id: UUID = Field(..., description="Trace ID (== query or ingestion ID).")
    trace_type: TraceType = Field(..., description="Trace discriminator; one of ``query``, ``ingestion``.")
    started_at: UtcDatetime
    finished_at: UtcDatetime
    total_latency_ms: float = Field(..., ge=0)
    stages: list[TraceStage] = Field(default_factory=list)
    error: str | None = Field(None, description="Top-level error message if any.")


__all__ = ["TraceResponse", "TraceStage", "TraceType"]
