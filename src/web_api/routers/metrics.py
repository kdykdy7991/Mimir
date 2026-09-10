"""Operational RAG overview metrics."""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from src.application.composition import ApplicationServices
from src.web_api.dependencies import get_application_services
from src.web_api.mappers import collection_uuid
from src.web_api.schemas.metrics import (
    KnowledgeBaseHealth,
    KnowledgeBaseMetric,
    OverviewAttentionItem,
    OverviewCoreMetrics,
    OverviewCorpus,
    OverviewMetric,
    OverviewResponse,
    OverviewTrendPoint,
    RetrievalHealth,
    TrafficMetrics,
)

router = APIRouter(prefix="/metrics", tags=["metrics"])

_RANGE_SECONDS = {"24h": 86_400, "7d": 604_800, "30d": 2_592_000}
_BUCKETS = {"24h": 6, "7d": 7, "30d": 6}


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 1) if denominator else None


def _embedding_token_metrics(
    services: ApplicationServices, start: float, now: float,
) -> dict[str, Any]:
    """Embedding token fields for the traffic card (PRD §4).

    The three token fields are a unit — all numeric or all ``null``:

    - Capability off (no usage store wired, or the provider can't report
      usage — local encoders) → all ``null``. We must not fabricate a
      number from text length.
    - Capability on → window sums straight from the ``embedding_usage_events``
      table; an empty window honestly reads ``0``. ``since`` is the earliest
      recorded event so the UI can flag partial-period data (``null`` until
      the first event lands, since stats have no data start yet).
    """
    usage = getattr(services, "usage", None)
    if usage is None or not getattr(usage, "enabled", False):
        return {
            "embedding_token_usage": None,
            "query_embedding_tokens": None,
            "ingestion_embedding_tokens": None,
            "embedding_token_usage_since": None,
        }
    total, query_total, ingestion_total = services.db.summarize_embedding_usage(
        start, now,
    )
    earliest = services.db.embedding_usage_earliest()
    return {
        "embedding_token_usage": total,
        "query_embedding_tokens": query_total,
        "ingestion_embedding_tokens": ingestion_total,
        "embedding_token_usage_since": (
            datetime.fromtimestamp(earliest, tz=timezone.utc)
            if earliest is not None else None
        ),
    }


def _decode_queries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decoded: list[dict[str, Any]] = []
    for row in rows:
        try:
            result = json.loads(row["result_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        decoded.append({
            **row,
            "has_results": bool(result.get("chunks")),
            "succeeded": not bool(result.get("error")),
            "degraded": bool(result.get("degraded")),
            "latency_ms": float(result.get("latency_ms") or 0.0),
        })
    return decoded


def _query_metrics(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    count = len(rows)
    succeeded = [row for row in rows if row["succeeded"]]
    no_results = sum(not row["has_results"] for row in succeeded)
    degraded = sum(row["degraded"] for row in rows)
    effective = sum(row["has_results"] and not row["degraded"] for row in succeeded)
    return {
        "count": count,
        "effective": _rate(effective, count),
        "no_result": _rate(no_results, count),
        "degraded": _rate(degraded, count),
        "p95": round(_p95([row["latency_ms"] for row in rows]) or 0.0, 1) if count else None,
    }


def _ingestion_metrics(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    terminal = [row for row in rows if row["status"] in {"succeeded", "failed", "cancelled"}]
    succeeded = sum(row["status"] == "succeeded" for row in terminal)
    failed = sum(row["status"] in {"failed", "cancelled"} for row in rows)
    return {"success": _rate(succeeded, len(terminal)), "failed": failed}


def _trend(
    rows: list[dict[str, Any]], start: float, end: float, bucket_count: int,
) -> list[OverviewTrendPoint]:
    width = (end - start) / bucket_count
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(bucket_count)]
    for row in rows:
        index = min(bucket_count - 1, max(0, int((row["created_at"] - start) / width)))
        buckets[index].append(row)
    points: list[OverviewTrendPoint] = []
    for index, bucket in enumerate(buckets):
        metrics = _query_metrics(bucket)
        points.append(OverviewTrendPoint(
            timestamp=datetime.fromtimestamp(start + index * width, tz=timezone.utc),
            query_count=int(metrics["count"] or 0),
            effective_retrieval_rate=metrics["effective"],
            p95_latency_ms=metrics["p95"],
        ))
    return points


@router.get("/overview", response_model=OverviewResponse, summary="RAG overview metrics")
def get_overview_metrics(
    range_: Literal["24h", "7d", "30d"] = Query("7d", alias="range"),
    services: ApplicationServices = Depends(get_application_services),
) -> OverviewResponse:
    """Aggregate all RAG query sources; MCP records are included in the total."""
    now = time.time()
    duration = _RANGE_SECONDS[range_]
    start = now - duration
    previous_start = start - duration
    db = services.db

    current_queries = _decode_queries(db.list_query_results_between(start, now))
    previous_queries = _decode_queries(db.list_query_results_between(previous_start, start))
    current_tasks = db.list_tasks_between(start, now, task_type="ingestion")
    previous_tasks = db.list_tasks_between(previous_start, start, task_type="ingestion")

    current = _query_metrics(current_queries)
    previous = _query_metrics(previous_queries)
    ingestion = _ingestion_metrics(current_tasks)
    previous_ingestion = _ingestion_metrics(previous_tasks)

    request_count = len(current_queries)
    previous_request_count = len(previous_queries)

    refs = services.document.list_collections()
    corpus_stats = services.document.get_corpus_overview_stats(
        [ref.name for ref in refs],
    )
    corpus_documents = corpus_stats.n_documents
    corpus_chunks = corpus_stats.n_chunks
    document_statuses = corpus_stats.statuses
    last_updated_at: float | None = None
    for ref in refs:
        if ref.updated_at is not None:
            last_updated_at = max(last_updated_at or ref.updated_at, ref.updated_at)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in current_queries:
        grouped[row["collection"]].append(row)
    knowledge_bases: list[KnowledgeBaseMetric] = []
    for name, rows in grouped.items():
        metrics = _query_metrics(rows)
        risky = bool(
            metrics["p95"] is not None and metrics["p95"] > 2000
        )
        knowledge_bases.append(KnowledgeBaseMetric(
            collection_id=collection_uuid(name),
            name=name,
            query_count=len(rows),
            no_result_rate=metrics["no_result"],
            p95_latency_ms=metrics["p95"],
            status="attention" if risky else "ok",
        ))
    knowledge_bases.sort(
        key=lambda item: (
            item.status == "attention",
            item.p95_latency_ms or 0,
            item.query_count,
        ),
        reverse=True,
    )

    health = services.system.get_health()
    attention: list[OverviewAttentionItem] = []
    if current["p95"] is not None and current["p95"] > 2000:
        attention.append(OverviewAttentionItem(
            severity="warning",
            title=f"P95 响应时间达到 {current['p95'] / 1000:.1f} 秒",
            description="查看慢查询并定位耗时阶段。",
            href="/traces?type=query",
        ))
    unhealthy = [item for item in health.dependencies if item.status != "ok"]
    if unhealthy:
        names = "、".join(item.name for item in unhealthy[:3])
        attention.append(OverviewAttentionItem(
            severity="critical" if any(item.status == "down" for item in unhealthy) else "warning",
            title=f"{names} 状态需要确认",
            description="依赖服务未就绪或健康状态尚未验证。",
            href="/overview",
        ))

    status_value: Literal["ok", "attention", "critical"] = "ok"
    if any(item.severity == "critical" for item in attention):
        status_value = "critical"
    elif attention:
        status_value = "attention"
    headline = {"ok": "RAG 运行正常", "attention": "RAG 需要关注", "critical": "RAG 存在异常"}[status_value]
    if not current_queries:
        summary = "当前周期暂无查询数据；系统仍持续监控文档处理与依赖状态。"
    elif attention:
        summary = attention[0].title + "，建议优先处理。"
    else:
        summary = "检索质量、响应时间和文档处理均处于目标范围。"

    return OverviewResponse(
        range=range_,
        started_at=datetime.fromtimestamp(start, tz=timezone.utc),
        ended_at=datetime.fromtimestamp(now, tz=timezone.utc),
        status=status_value,
        headline=headline,
        summary=summary,
        query_count=request_count,
        previous_query_count=previous_request_count,
        degraded_rate=current["degraded"],
        failed_ingestions=int(ingestion["failed"] or 0),
        corpus=OverviewCorpus(
            collection_count=len(refs),
            document_count=corpus_documents,
            chunk_count=corpus_chunks,
        ),
        traffic=TrafficMetrics(
            request_count=request_count,
            previous_request_count=previous_request_count,
            success_rate=_rate(sum(row["succeeded"] for row in current_queries), request_count),
            average_latency_ms=(
                round(sum(row["latency_ms"] for row in current_queries) / len(current_queries), 1)
                if current_queries else None
            ),
            **_embedding_token_metrics(services, start, now),
        ),
        retrieval_health=RetrievalHealth(
            success_rate=_rate(sum(row["has_results"] for row in current_queries if row["succeeded"]), sum(row["succeeded"] for row in current_queries)),
            empty_retrieval_rate=current["no_result"],
            average_top_k=(
                round(sum(len(json.loads(row["result_json"]).get("chunks", [])) for row in current_queries) / len(current_queries), 1)
                if current_queries else None
            ),
            average_latency_ms=(
                round(sum(row["latency_ms"] for row in current_queries) / len(current_queries), 1)
                if current_queries else None
            ),
            rerank_success_rate=None,
        ),
        knowledge_base_health=KnowledgeBaseHealth(
            document_count=corpus_documents,
            chunk_count=corpus_chunks,
            index_status="attention" if "failed" in document_statuses else "indexing" if any(status != "success" for status in document_statuses) else "ready",
            last_updated_at=datetime.fromtimestamp(last_updated_at, tz=timezone.utc) if last_updated_at is not None else None,
        ),
        metrics=OverviewCoreMetrics(
            effective_retrieval_rate=OverviewMetric(value=current["effective"], previous=previous["effective"], target=90),
            p95_latency_ms=OverviewMetric(value=current["p95"], previous=previous["p95"], target=2000),
            no_result_rate=OverviewMetric(value=current["no_result"], previous=previous["no_result"], target=10),
            ingestion_success_rate=OverviewMetric(value=ingestion["success"], previous=previous_ingestion["success"], target=99),
        ),
        trend=_trend(current_queries, start, now, _BUCKETS[range_]),
        attention=attention[:5],
        knowledge_bases=knowledge_bases[:5],
    )


__all__ = ["router"]
