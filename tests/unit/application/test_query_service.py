"""
Unit tests for ``QueryService`` (M1 application layer).

The service is a thin facade over ``HybridSearch``, so we drive it
with a recording fake and assert delegation + latency reporting —
no RAG engine behaviour is re-tested here.
"""

from __future__ import annotations

import pytest

from src.application.services import QueryResult, QueryService


class _FakeResult:
    """Stand-in for a ``RetrievalResult`` — the service only forwards."""

    def __init__(self, chunk_id: str) -> None:
        self.chunk_id = chunk_id


class _FakeHybridSearch:
    """Records calls; returns a fixed candidate list."""

    def __init__(self, results: list | None = None) -> None:
        self._results = results or []
        self.calls: list[dict] = []

    def search(
        self, *, query: str, top_k: int = 5, filters: dict | None = None,
        trace=None,
    ) -> list:
        self.calls.append({
            "query": query,
            "top_k": top_k,
            "filters": filters,
            "trace": trace,
        })
        return self._results


@pytest.fixture
def hybrid() -> _FakeHybridSearch:
    return _FakeHybridSearch([_FakeResult("c1"), _FakeResult("c2")])


@pytest.fixture
def svc(hybrid: _FakeHybridSearch) -> QueryService:
    return QueryService(hybrid)


class TestConstruction:
    def test_exposes_collaborator(self, hybrid: _FakeHybridSearch) -> None:
        svc = QueryService(hybrid)
        assert svc.hybrid_search is hybrid


class _FakeEngineCache:
    """Records which collections are requested; returns per-collection fakes."""

    def __init__(self) -> None:
        self.requested: list[str] = []
        self.engines: dict[str, _FakeHybridSearch] = {}

    def hybrid_for(self, collection: str) -> _FakeHybridSearch:
        self.requested.append(collection)
        if collection not in self.engines:
            self.engines[collection] = _FakeHybridSearch(
                [_FakeResult(f"c-{collection}")],
            )
        return self.engines[collection]


class TestEngineCacheRouting:
    """M3: a cache-backed QueryService routes ``collection`` per request."""

    def test_search_routes_by_collection(self) -> None:
        cache = _FakeEngineCache()
        svc = QueryService(cache)
        result = svc.search("q", collection="reports")
        assert cache.requested == ["reports"]
        assert [c.chunk_id for c in result.chunks] == ["c-reports"]

    def test_search_defaults_to_default_collection(self) -> None:
        cache = _FakeEngineCache()
        svc = QueryService(cache)
        svc.search("q")
        assert cache.requested == ["default"]

    def test_distinct_collections_get_distinct_engines(self) -> None:
        cache = _FakeEngineCache()
        svc = QueryService(cache)
        svc.search("q", collection="a")
        svc.search("q", collection="b")
        assert cache.requested == ["a", "b"]
        assert cache.engines["a"] is not cache.engines["b"]

    def test_hybrid_search_property_rejects_cache_backed_service(self) -> None:
        svc = QueryService(_FakeEngineCache())
        with pytest.raises(TypeError):
            svc.hybrid_search


class TestSearch:
    def test_returns_chunks_with_latency(self, svc: QueryService) -> None:
        result = svc.search("测试查询", top_k=3)
        assert isinstance(result, QueryResult)
        assert [c.chunk_id for c in result.chunks] == ["c1", "c2"]
        # latency_ms is a non-negative float measured around the call.
        assert result.latency_ms >= 0.0

    def test_delegates_with_defaults(self, hybrid: _FakeHybridSearch) -> None:
        QueryService(hybrid).search("hello")
        call = hybrid.calls[0]
        assert call["query"] == "hello"
        assert call["top_k"] == 5
        assert call["filters"] is None
        # M2 batch 3: the service creates its own query trace when the
        # caller doesn't inject one (query_id == trace_id for the Web API).
        assert call["trace"] is not None

    def test_passes_filters_and_trace(
        self, hybrid: _FakeHybridSearch,
    ) -> None:
        from src.core.trace.trace_context import TraceContext

        trace = TraceContext()
        QueryService(hybrid).search(
            "q", top_k=9, filters={"collection": "x"}, trace=trace,
        )
        call = hybrid.calls[0]
        assert call["top_k"] == 9
        assert call["filters"] == {"collection": "x"}
        assert call["trace"] is trace

    def test_result_exposes_trace_id(self, svc: QueryService) -> None:
        result = svc.search("hello")
        assert result.trace_id is not None
        assert len(result.trace_id) == 36  # uuid4

    def test_empty_results_still_returned(self, hybrid: _FakeHybridSearch) -> None:
        hybrid._results = []
        result = QueryService(hybrid).search("nothing")
        assert result.chunks == []


__all__ = []
