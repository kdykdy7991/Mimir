"""DTOs for the operational RAG overview."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.web_api.schemas._types import UtcDatetime


class OverviewMetric(BaseModel):
    """Current and previous-period value for one decision metric."""

    value: float | None = None
    previous: float | None = None
    target: float | None = None


class OverviewCoreMetrics(BaseModel):
    effective_retrieval_rate: OverviewMetric
    p95_latency_ms: OverviewMetric
    no_result_rate: OverviewMetric
    ingestion_success_rate: OverviewMetric


class OverviewTrendPoint(BaseModel):
    timestamp: UtcDatetime
    query_count: int = 0
    effective_retrieval_rate: float | None = None
    p95_latency_ms: float | None = None


class TrafficMetrics(BaseModel):
    request_count: int
    previous_request_count: int
    success_rate: float | None = None
    average_latency_ms: float | None = None
    embedding_token_usage: int | None = Field(
        None, description="Embedding input tokens consumed by query and document indexing operations.",
    )


class RetrievalHealth(BaseModel):
    success_rate: float | None = None
    empty_retrieval_rate: float | None = None
    average_top_k: float | None = None
    average_latency_ms: float | None = None
    rerank_success_rate: float | None = Field(
        None, description="Unavailable while reranking is not enabled/instrumented.",
    )


class KnowledgeBaseHealth(BaseModel):
    document_count: int
    chunk_count: int
    index_status: Literal["ready", "indexing", "attention"]
    last_updated_at: UtcDatetime | None = None


class OverviewAttentionItem(BaseModel):
    severity: Literal["warning", "critical"]
    title: str
    description: str
    href: str


class KnowledgeBaseMetric(BaseModel):
    collection_id: UUID
    name: str
    query_count: int
    no_result_rate: float | None = None
    p95_latency_ms: float | None = None
    status: Literal["ok", "attention"]


class OverviewCorpus(BaseModel):
    collection_count: int
    document_count: int
    chunk_count: int


class OverviewResponse(BaseModel):
    range: Literal["24h", "7d", "30d"]
    started_at: UtcDatetime
    ended_at: UtcDatetime
    status: Literal["ok", "attention", "critical"]
    headline: str
    summary: str
    query_count: int
    previous_query_count: int
    degraded_rate: float | None = None
    failed_ingestions: int
    corpus: OverviewCorpus
    traffic: TrafficMetrics
    retrieval_health: RetrievalHealth
    knowledge_base_health: KnowledgeBaseHealth
    metrics: OverviewCoreMetrics
    trend: list[OverviewTrendPoint]
    attention: list[OverviewAttentionItem]
    knowledge_bases: list[KnowledgeBaseMetric]


__all__ = [
    "KnowledgeBaseMetric",
    "OverviewAttentionItem",
    "OverviewCoreMetrics",
    "OverviewCorpus",
    "KnowledgeBaseHealth",
    "OverviewMetric",
    "OverviewResponse",
    "OverviewTrendPoint",
    "RetrievalHealth",
    "TrafficMetrics",
]
