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
from src.web_api.schemas.common import Page


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
    # B3.1 — actionable stage state. All optional so old JSONL lines with
    # fewer fields deserialise (missing fields derive to defaults/markings by
    # the mapper; ``None`` is a valid absence).
    status: Literal[
        "pending", "running", "success", "warning", "failed", "skipped", "canceled",
    ] | None = Field(
        None,
        description=(
            "Lifecycle status of this stage. Derived for old traces: "
            "``success`` for a completed stage, ``failed``/``skipped``/"
            "``canceled`` from a terminal event tag, ``running`` when still "
            "in flight."
        ),
    )
    input_count: int | None = Field(
        None, ge=0, description="Items processed into this stage (``None`` when unknown).",
    )
    output_count: int | None = Field(
        None, ge=0, description="Items produced by this stage (``None`` when unknown).",
    )
    attempt: int | None = Field(
        None, ge=0, description="Retry attempt this stage belongs to (0 for first try).",
    )
    skip_reason: str | None = Field(
        None, description="Short reason when the stage was skipped.",
    )
    error_code: str | None = Field(
        None, description="Stable error code when the stage failed (branchable).",
    )
    error_summary: str | None = Field(
        None, description="Sanitised, human-readable error summary (never raw secrets).",
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
    # B3.1 — actionable top-level trace state. Missing on old JSONL and
    # derived by the mapper; ``status`` derives from stages / task status.
    status: Literal[
        "pending", "running", "success", "warning", "failed", "skipped", "canceled",
    ] | None = Field(
        None,
        description=(
            "Lifecycle status. ``running``/``pending`` while in flight; "
            "``success``/``failed``/``canceled``/``skipped`` once terminal."
        ),
    )
    retryable: bool = Field(
        False,
        description="True when this trace may be retried (status failed/canceled).",
    )
    cancelable: bool = Field(
        False,
        description="True when this trace may be cancelled (status pending/running).",
    )
    attempt: int | None = Field(
        None, ge=0, description="Retry attempt (0 for the original run).",
    )
    parent_trace_id: str | None = Field(
        None, description="Trace id of the failed run this attempt retried (null for the root).",
    )


class TraceListResponse(Page[TraceResponse]):
    """``GET /traces`` response — cursor-paginated, newest-first."""


__all__ = ["TraceResponse", "TraceListResponse", "TraceStage", "TraceType"]
