"""
Unit tests for ``src/application/engines.py`` — the M3 ``EngineCache``.

The cache lazily builds one ``HybridSearch`` / ``IngestionPipeline`` per
collection. The heavy construction (``scripts.query.build_query_components``
/ ``scripts.ingest.build_pipeline``) is exercised elsewhere; here we pin
the M3-specific guarantees:

- ``_scoped_store`` wraps the shared router in a per-collection
  ``ScopedCollectionVectorStore`` so DenseRetriever / SparseRetriever /
  VectorUpserter calls (which carry no ``collection`` kwarg) route to the
  right Chroma store.
- per-collection engines are cached (one build per collection).
"""

from __future__ import annotations

import pytest

from src.application.engines import EngineCache
from src.core.settings import Settings
from src.libs.vector_store.scoped import ScopedCollectionVectorStore


class _StubRouter:
    """Minimal stand-in for ``MultiCollectionVectorStore`` — construction
    only, no chromadb dependency."""


class _StubEmbedding:
    """Stand-in for the shared embedding model — never invoked here."""


@pytest.fixture
def cache(tmp_path) -> EngineCache:
    router = _StubRouter()
    return EngineCache(
        settings=Settings(),
        data_dir=str(tmp_path),
        embedding=_StubEmbedding(),
        vector_store=router,
    )


class TestScopedStore:
    def test_wraps_router_bound_to_collection(self, cache: EngineCache) -> None:
        scoped = cache._scoped_store("reports")
        assert isinstance(scoped, ScopedCollectionVectorStore)
        assert scoped._router is cache.vector_store
        assert scoped._collection == "reports"

    def test_distinct_collections_get_distinct_scopes(self, cache: EngineCache) -> None:
        a = cache._scoped_store("a")
        b = cache._scoped_store("b")
        assert a._collection == "a"
        assert b._collection == "b"


class TestEngineCaching:
    def test_hybrid_for_caches_per_collection(self, cache: EngineCache) -> None:
        h1 = cache.hybrid_for("reports")
        h2 = cache.hybrid_for("reports")
        assert h1 is h2
        other = cache.hybrid_for("finance")
        assert other is not h1

    def test_pipeline_for_caches_per_collection(self, cache: EngineCache) -> None:
        p1 = cache.pipeline_for("reports")
        p2 = cache.pipeline_for("reports")
        assert p1 is p2
        other = cache.pipeline_for("finance")
        assert other is not p1


__all__ = []
