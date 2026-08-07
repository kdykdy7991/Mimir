"""
``QueryService`` — application-layer entry point for retrieval queries.

M1 职责（v0.1 契约）

- 薄封装，**不改变** ``HybridSearch`` 的行为。
- 给 CLI / MCP / Streamlit / Web API 四个入口一个稳定的依赖项，
  避免各入口直接 import ``HybridSearch``。

M2 批次 3 扩展（v0.2 契约）

- ``mode`` 派发：``hybrid``（默认，走完整 ``HybridSearch`` 流水线）、
  ``dense`` / ``sparse``（直接调用引擎的单路 retriever）。dense/sparse
  是「绕过融合」的捷径而不是新的索引。

M3 扩展（多 collection）

- 服务可接收一个 :class:`EngineCache`（Web API boot 注入），
  ``search(collection=...)`` 路由到该集合的 ``HybridSearch``——修复
  v0.2 中非 default 集合静默查 default 索引的限制。CLI / MCP 仍以
  单个 ``HybridSearch`` 构造（忽略 ``collection`` 参数）。
- Trace：默认创建一个 ``TRACE_TYPE_QUERY`` 的 ``TraceContext`` 并传入
  引擎；完成后经可选的 ``trace_store`` 落库（``query_id == trace_id``）。
  调用方仍可注入自己的 ``trace``（CLI/MCP 既有行为不变）。
- ``QueryResult`` 携带 ``trace_id`` / ``degraded`` / ``degraded_reasons``
  和诊断计数，Web API 的 mapper 直接消费。

``enable_rerank`` 在 v0.2 为 no-op（rerank 尚未接入 ``HybridSearch`` 流水线，
见 ``docs/plan-2026-07-31-m2-batch3.md`` §4.2）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from src.application.identifiers import collection_uuid, document_uuid
from src.core.trace.trace_context import (
    TRACE_TYPE_QUERY,
    TraceContext,
    new_trace,
)
from src.core.types import RetrievalResult
from src.application.services.task_types import TaskError
from src.libs.embedding.usage import embedding_usage_context

if TYPE_CHECKING:
    from src.application.services.trace_store import TraceStore
    from src.core.query_engine.hybrid_search import HybridSearch

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    """Application-layer query result — wraps the engine's search() return."""

    chunks: list  # list[RetrievalResult]
    latency_ms: float = 0.0
    trace_id: str | None = None
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)
    dense_count: int | None = None
    sparse_count: int | None = None
    fused_count: int | None = None


@dataclass
class AsyncQueryState:
    """Snapshot of an async query task (M3 batch 2).

    Returned by :meth:`QueryService.get_async_result` for the
    ``GET /queries/{query_id}/result`` polling endpoint. ``status`` is
    ``pending`` / ``running`` / ``succeeded`` / ``failed``; the result
    is populated only on success, ``error`` only on failure.
    """

    status: str
    query_id: str
    result: QueryResult | None = None
    collection: str | None = None
    error: TaskError | None = None


def _query_result_to_dict(result: QueryResult) -> dict[str, Any]:
    """Serialize a ``QueryResult`` for durable storage."""
    return {
        "chunks": [c.to_dict() for c in result.chunks],
        "latency_ms": result.latency_ms,
        "trace_id": result.trace_id,
        "degraded": result.degraded,
        "degraded_reasons": list(result.degraded_reasons),
        "dense_count": result.dense_count,
        "sparse_count": result.sparse_count,
        "fused_count": result.fused_count,
    }


def _query_result_from_dict(data: dict[str, Any]) -> QueryResult:
    """Rebuild a ``QueryResult`` from the serialized form."""
    return QueryResult(
        chunks=[
            RetrievalResult.from_dict(c) for c in data.get("chunks", [])
        ],
        latency_ms=data.get("latency_ms", 0.0),
        trace_id=data.get("trace_id"),
        degraded=data.get("degraded", False),
        degraded_reasons=list(data.get("degraded_reasons", [])),
        dense_count=data.get("dense_count"),
        sparse_count=data.get("sparse_count"),
        fused_count=data.get("fused_count"),
    )


def _query_task_error(exc: Exception) -> TaskError:
    """Map an async query failure to the v0.1 error vocabulary."""
    from src.libs.embedding import EmbeddingError

    if isinstance(exc, EmbeddingError):
        return TaskError(
            code="UPSTREAM_ERROR",
            message="embedding provider failed",
            details={},
        )
    return TaskError(
        code="INTERNAL_ERROR",
        message="query failed",
        details={"exception_type": type(exc).__name__},
    )


# Trace events that mean "something fell back". Matches what
# HybridSearch / DenseRetriever / SparseRetriever actually record.
_DEGRADED_EVENTS = frozenset({
    "dense_failed", "sparse_failed", "no_results",
    "embedding_error", "store_error", "query_error", "index_missing",
})

_DEGRADED_REASON_MESSAGES: dict[str, str] = {
    "dense_failed": "dense retrieval failed; falling back to remaining paths",
    "sparse_failed": "sparse retrieval failed; falling back to remaining paths",
    "no_results": "no chunks matched the query",
    "embedding_error": "embedding provider failed",
    "store_error": "vector store query failed",
    "query_error": "sparse/BM25 query failed",
    "index_missing": "no index on disk for this collection",
}


def _degraded_reasons(trace: TraceContext) -> list[str]:
    """Human-readable degraded reasons, deduped, in trace order."""
    seen: list[str] = []
    for stage in trace.stages:
        event = stage.get("event")
        if event not in _DEGRADED_EVENTS:
            continue
        msg = _DEGRADED_REASON_MESSAGES.get(event, event)
        if msg not in seen:
            seen.append(msg)
    return seen


def _stage_int(trace: TraceContext, name: str, key: str) -> int | None:
    """Pull an int key off the latest matching trace stage (best-effort)."""
    for stage in reversed(trace.stages):
        if stage.get("name") == name and key in stage:
            value = stage.get(key)
            if value is not None:
                return int(value)
    return None


class QueryService:
    """Facade over :class:`HybridSearch`.

    Constructed once at app boot; shared by CLI / MCP / Streamlit / Web
    API. The optional ``trace_store`` is wired by the Web API composition
    root so every query leaves a retrievable trace.

    ``engines`` is duck-typed:

    - a single :class:`HybridSearch` (CLI / MCP / tests) — every call
      uses it regardless of ``collection``;
    - an :class:`EngineCache` (Web API, M3) — :meth:`search` routes
      ``collection`` to the per-collection engine via
      ``EngineCache.hybrid_for``, fixing the v0.2 limitation where
      non-default collections silently queried the default index.

    ``tracker`` / ``db`` (Web API boot, M3 batch 2) enable async
    queries: ``tracker`` runs the pending → running → succeeded/failed
    state machine and ``db`` persists query results + document
    references (feeding ``GET /queries/{query_id}/result`` and
    ``DocumentDetail.last_query_id``). CLI / MCP leave both ``None``.
    """

    def __init__(
        self,
        engines: "HybridSearch | Any",
        trace_store: "TraceStore | None" = None,
        *,
        tracker: Any | None = None,
        db: Any | None = None,
    ) -> None:
        self._engines = engines
        self._trace_store = trace_store
        self._tracker = tracker
        self._db = db

    @property
    def hybrid_search(self) -> "HybridSearch":
        """The single ``HybridSearch`` (legacy single-engine construction).

        Raises ``TypeError`` when this service was built with an
        :class:`EngineCache` — use :meth:`search` with ``collection=``
        to route there.
        """
        if hasattr(self._engines, "hybrid_for"):
            raise TypeError(
                "QueryService was built with an EngineCache; "
                "route via search(collection=...).",
            )
        return self._engines

    @property
    def trace_store(self) -> "TraceStore | None":
        return self._trace_store

    def _engine_for(self, collection: str) -> "HybridSearch":
        """Resolve the engine serving ``collection``.

        A single-engine service ignores ``collection`` and returns the
        one engine; a cache-backed service dispatches per collection.
        """
        if hasattr(self._engines, "hybrid_for"):
            return self._engines.hybrid_for(collection)
        return self._engines

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        mode: str = "hybrid",
        filters: dict | None = None,
        trace: TraceContext | None = None,
        collection: str = "default",
    ) -> QueryResult:
        """Run retrieval and return chunks.

        ``mode`` is one of ``"hybrid"`` (default), ``"dense"`` or
        ``"sparse"``. Hybrid runs the full pipeline; dense / sparse
        bypass fusion via the engine's single-path retrievers.

        ``collection`` selects the per-collection engine when this
        service was built with an :class:`EngineCache`; it is ignored
        by the single-engine construction (CLI / MCP).

        ``trace`` is the caller's own ``TraceContext`` if they want to
        control it (CLI / MCP); otherwise one is created here. When a
        ``trace_store`` is configured the finished trace is recorded,
        making ``result.trace_id`` fetchable via the Web API.
        """
        engine = self._engine_for(collection)
        trace = trace or new_trace(TRACE_TYPE_QUERY)
        start = time.perf_counter()
        # PRD §5.2: tag this query's embedding calls as ``query`` so the
        # usage listener buckets the tokens correctly. The context only
        # covers the engine invocation — nothing else in this method
        # embeds. ContextVars are per-thread; async queries run ``search``
        # in their worker thread, which is where the context is set+read.
        with embedding_usage_context(
            operation="query",
            collection_id=str(collection_uuid(collection)),
            trace_id=trace.trace_id,
        ):
            if mode == "dense":
                results = self._dense_search(engine, query, top_k, filters, trace)
            elif mode == "sparse":
                results = self._sparse_search(engine, query, top_k, trace)
            else:
                results = engine.search(
                    query=query, top_k=top_k, filters=filters, trace=trace,
                )
        latency_ms = (time.perf_counter() - start) * 1000.0

        trace.finish()
        if self._trace_store is not None:
            self._trace_store.record(trace)

        result = QueryResult(
            chunks=list(results),
            latency_ms=latency_ms,
            trace_id=trace.trace_id,
            degraded=bool(_degraded_reasons(trace)),
            degraded_reasons=_degraded_reasons(trace),
            dense_count=_stage_int(trace, "dense_retriever", "n_out"),
            sparse_count=_stage_int(trace, "sparse_retriever", "n_out"),
            fused_count=_stage_int(trace, "trim", "n_before"),
        )
        # M3 batch 2: persist the query output + document references so
        # ``GET /queries/{id}/result`` (async) and ``last_query_id``
        # (document detail) have something to read. Best-effort.
        self._record_query_result(query, collection, result)
        return result

    # ------------------------------------------------------------------
    # Async queries (M3 batch 2) — POST /collections/{id}/queries/async
    # ------------------------------------------------------------------
    def search_async(
        self,
        query: str,
        *,
        top_k: int = 5,
        mode: str = "hybrid",
        filters: dict | None = None,
        collection: str = "default",
    ) -> UUID:
        """Kick off a query in a background thread; return the task id.

        ``task_id`` doubles as the ``query_id`` (and the trace id), so
        the client polls ``GET /queries/{query_id}/result`` and can
        fetch ``GET /queries/{query_id}/trace`` with the same id.

        Requires the Web API wiring (``tracker`` + ``db``); raises
        ``ValueError`` otherwise.
        """
        if self._tracker is None or self._db is None:
            raise ValueError(
                "async queries require the Web API wiring "
                "(tracker + WebApiDB)",
            )
        task_id = uuid4()
        self._tracker.create(
            task_id=task_id,
            task_type="query",
            document_id=None,
            collection_id=collection_uuid(collection),
            source_path="",
            filename="",
        )
        worker = threading.Thread(
            target=self._run_async_query,
            args=(task_id, query, top_k, mode, filters, collection),
            name=f"query-{task_id}",
            daemon=True,
        )
        worker.start()
        return task_id

    def get_async_result(self, query_id: UUID) -> AsyncQueryState | None:
        """Snapshot of an async query — ``None`` when the task is unknown."""
        if self._tracker is None:
            return None
        rec = self._tracker.get(query_id)
        if rec is None:
            return None
        if rec.status in ("pending", "running"):
            return AsyncQueryState(status=rec.status, query_id=str(query_id))
        if rec.status == "failed":
            return AsyncQueryState(
                status="failed", query_id=str(query_id), error=rec.error,
            )
        # succeeded — read the persisted result (survives a restart).
        data = self._db.get_query_result(str(query_id)) if self._db else None
        if data is None:
            return AsyncQueryState(status="succeeded", query_id=str(query_id))
        return AsyncQueryState(
            status="succeeded",
            query_id=str(query_id),
            collection=data.get("collection"),
            result=_query_result_from_dict(json.loads(data["result_json"])),
        )

    def last_query_for_document(self, document_id: UUID) -> UUID | None:
        """Most recent query id that cited ``document_id`` (or ``None``).

        Feeds ``DocumentDetail.last_query_id`` — lets the UI jump from a
        document back to the last query that referenced it.
        """
        if self._db is None:
            return None
        qid = self._db.last_query_for_document(str(document_id))
        if qid is None:
            return None
        try:
            return UUID(qid)
        except ValueError:
            return None

    def _run_async_query(
        self,
        task_id: UUID,
        query: str,
        top_k: int,
        mode: str,
        filters: dict | None,
        collection: str,
    ) -> None:
        """Thread body: run the query and flip the task to terminal."""
        self._tracker.update(task_id, lambda rec: rec.mark_started())
        try:
            # trace_id == task_id so ``GET /queries/{task_id}/trace``
            # works for async queries too.
            trace = TraceContext(
                trace_id=str(task_id), trace_type=TRACE_TYPE_QUERY,
            )
            self.search(
                query=query, top_k=top_k, mode=mode, filters=filters,
                collection=collection, trace=trace,
            )
            self._tracker.update(task_id, lambda rec: rec.mark_succeeded())
        except Exception as exc:  # noqa: BLE001
            logger.warning("async query %s failed: %s", task_id, exc)
            self._tracker.update(
                task_id, lambda rec: rec.mark_failed(_query_task_error(exc)),
            )

    def _record_query_result(
        self, query: str, collection: str, result: QueryResult,
    ) -> None:
        """Persist query output + cited document ids (best-effort)."""
        if self._db is None or result.trace_id is None:
            return
        doc_ids = sorted({
            str(document_uuid(collection, chunk.metadata.get("source_path", "")))
            for chunk in result.chunks
            if chunk.metadata.get("source_path")
        })
        try:
            self._db.save_query_result(
                query_id=result.trace_id,
                collection=collection,
                query_text=query,
                result_json=json.dumps(
                    _query_result_to_dict(result),
                    ensure_ascii=False, default=str,
                ),
                document_ids=doc_ids,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "failed to record query result %s", result.trace_id,
            )

    # ------------------------------------------------------------------
    # Mode shortcuts (bypass fusion)
    # ------------------------------------------------------------------
    def _dense_search(
        self,
        engine: "HybridSearch",
        query: str,
        top_k: int,
        filters: dict | None,
        trace: TraceContext,
    ) -> list:
        """Dense-only: vector-store retrieval on the raw query text."""
        return engine.dense.retrieve(
            query, top_k, filters, trace=trace,
        )

    def _sparse_search(
        self,
        engine: "HybridSearch",
        query: str,
        top_k: int,
        trace: TraceContext,
    ) -> list:
        """Sparse-only: BM25 retrieval on the extracted keywords."""
        processed = engine.query_processor.process(query)
        return engine.sparse.retrieve(
            processed.keywords, top_k, trace=trace,
        )


__all__ = ["AsyncQueryState", "QueryResult", "QueryService"]
