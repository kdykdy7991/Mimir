"""
Integration-style tests for ``MultiCollectionVectorStore`` (M3).

Uses a real ``chromadb.PersistentClient`` against a ``tmp_path``-backed
persist dir to prove the core M3 guarantee: upserts and queries are
isolated per collection. The Web API's ``EngineCache`` wires this router
behind per-collection ``ScopedCollectionVectorStore`` adapters, so the
DenseRetriever / SparseRetriever / VectorUpserter calls (which carry no
``collection`` kwarg) route to the right Chroma collection.

Requires the ``chromadb`` package (skip otherwise).
"""

from __future__ import annotations

import re

import pytest

from src.core.settings import VectorStoreSettings
from src.libs.vector_store.base_vector_store import VectorRecord
from src.libs.vector_store.chroma_store import chroma_collection_name
from src.libs.vector_store.collection_router import MultiCollectionVectorStore
from src.libs.vector_store.scoped import ScopedCollectionVectorStore

chromadb = pytest.importorskip("chromadb")


@pytest.fixture
def router(tmp_path) -> MultiCollectionVectorStore:
    settings = VectorStoreSettings(
        backend="chroma",
        persist_path=str(tmp_path / "chroma"),
    )
    return MultiCollectionVectorStore(settings)


class TestCollectionIsolation:
    def test_upsert_and_query_are_isolated_per_collection(
        self, router: MultiCollectionVectorStore,
    ) -> None:
        router.upsert(
            [VectorRecord(
                id="a1", vector=[0.1, 0.2], text="reports chunk",
                metadata={"source_path": "/r.pdf"},
            )],
            collection="reports",
        )
        router.upsert(
            [VectorRecord(
                id="d1", vector=[0.3, 0.4], text="default chunk",
                metadata={"source_path": "/d.pdf"},
            )],
            collection="default",
        )

        # Querying "reports" only sees the reports record.
        hits = router.query(vector=[0.1, 0.2], top_k=5, collection="reports")
        assert [h.id for h in hits] == ["a1"]
        assert router.count(collection="reports") == 1

        # Querying "default" only sees the default record.
        hits = router.query(vector=[0.1, 0.2], top_k=5, collection="default")
        assert [h.id for h in hits] == ["d1"]
        assert router.count(collection="default") == 1

    def test_get_by_metadata_is_scoped(self, router: MultiCollectionVectorStore) -> None:
        router.upsert(
            [VectorRecord(
                id="a1", vector=[0.1, 0.2], text="x",
                metadata={"source_path": "/r.pdf"},
            )],
            collection="reports",
        )
        # Same metadata key lives only in "reports".
        assert len(router.get_by_metadata(
            {"source_path": "/r.pdf"}, collection="reports",
        )) == 1
        assert len(router.get_by_metadata(
            {"source_path": "/r.pdf"}, collection="default",
        )) == 0

    def test_delete_is_scoped(self, router: MultiCollectionVectorStore) -> None:
        router.upsert(
            [VectorRecord(
                id="a1", vector=[0.1], text="x",
                metadata={"source_path": "/r.pdf"},
            )],
            collection="reports",
        )
        router.upsert(
            [VectorRecord(
                id="d1", vector=[0.1], text="x",
                metadata={"source_path": "/d.pdf"},
            )],
            collection="default",
        )
        assert router.delete(["a1"], collection="reports") == 1
        assert router.count(collection="reports") == 0
        assert router.count(collection="default") == 1


class TestScopedAdapter:
    def test_scoped_store_routes_to_its_collection(
        self, router: MultiCollectionVectorStore,
    ) -> None:
        router.upsert(
            [VectorRecord(
                id="a1", vector=[0.1, 0.2], text="reports chunk",
                metadata={"source_path": "/r.pdf"},
            )],
            collection="reports",
        )
        router.upsert(
            [VectorRecord(
                id="d1", vector=[0.3, 0.4], text="default chunk",
                metadata={"source_path": "/d.pdf"},
            )],
            collection="default",
        )

        # The adapter forwards every call with ``collection="reports"`` —
        # exactly what DenseRetriever / SparseRetriever / VectorUpserter
        # need (they pass no ``collection`` kwarg).
        scoped = ScopedCollectionVectorStore(router, "reports")
        assert [h.id for h in scoped.query(vector=[0.1, 0.2], top_k=5)] == ["a1"]
        assert len(scoped.get_by_ids(["a1"])) == 1

        # An "upsert through the adapter" lands in the bound collection.
        scoped.upsert(
            [VectorRecord(
                id="a2", vector=[0.5, 0.6], text="more reports",
                metadata={"source_path": "/r2.pdf"},
            )],
        )
        assert router.count(collection="reports") == 2
        assert router.count(collection="default") == 1


# ---------------------------------------------------------------------------
# chroma_collection_name — display name → Chroma-safe internal name
# ---------------------------------------------------------------------------

class TestChromaCollectionName:
    def test_valid_ascii_names_pass_through(self) -> None:
        # Existing / greppable names keep their literal Chroma name —
        # zero migration for collections already on disk.
        assert chroma_collection_name("default") == "default"
        assert chroma_collection_name("test") == "test"
        assert chroma_collection_name("a_b-c.d") == "a_b-c.d"

    def test_chinese_name_maps_to_deterministic_slug(self) -> None:
        a = chroma_collection_name("测试rag用例")
        b = chroma_collection_name("测试rag用例")
        # Same display name always resolves to the same Chroma name.
        assert a == b
        assert a != "测试rag用例"
        assert a.startswith("coll_")
        assert len(a) <= 512

    def test_distinct_names_map_distinctly(self) -> None:
        assert (
            chroma_collection_name("测试rag用例")
            != chroma_collection_name("中文知识库")
        )

    def test_too_short_and_edge_punctuation_map_to_slug(self) -> None:
        # Chroma requires 3-512 chars with alphanumeric first/last.
        for name in ("a", "ab", ".leading", "-dash", "end_"):
            mapped = chroma_collection_name(name)
            assert mapped != name
            assert mapped.startswith("coll_")

    def test_every_mapped_name_is_chroma_safe(self) -> None:
        # Mirror ChromaDB's validation rule exactly.
        safe = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,510}[a-zA-Z0-9]$")
        for name in ("测试rag用例", "中文知识库", "a", "ab", ".leading",
                     "太长的" * 100, "a_b", "x-y.z"):
            mapped = chroma_collection_name(name)
            assert safe.match(mapped), mapped
            assert 3 <= len(mapped) <= 512


# ---------------------------------------------------------------------------
# Non-ASCII (Chinese) collections through the router — M4 fix
# ---------------------------------------------------------------------------

class TestChineseCollectionRouting:
    def test_chinese_collection_upserts_and_queries(
        self, router: MultiCollectionVectorStore,
    ) -> None:
        router.upsert(
            [VectorRecord(
                id="c1", vector=[0.1, 0.2], text="你好世界",
                metadata={"source_path": "/中文文档.pdf"},
            )],
            collection="测试rag用例",
        )
        assert router.count(collection="测试rag用例") == 1
        hits = router.query(vector=[0.1, 0.2], top_k=5, collection="测试rag用例")
        assert [h.id for h in hits] == ["c1"]

    def test_chinese_collection_uses_mapped_chroma_name(
        self, router: MultiCollectionVectorStore,
    ) -> None:
        router.upsert(
            [VectorRecord(
                id="c1", vector=[0.1], text="x",
                metadata={"source_path": "/x.pdf"},
            )],
            collection="测试rag用例",
        )
        # Inspect via the router's own client (chromadb caches one
        # PersistentClient per path — opening a second one raises).
        names = [c.name for c in router._client.list_collections()]
        assert chroma_collection_name("测试rag用例") in names
        assert "测试rag用例" not in names


__all__ = []
