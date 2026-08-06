"""
Contract tests for VectorStore abstract interface.

Tests validate the input/output shape contract for all VectorStore implementations.
"""

from __future__ import annotations

import pytest

from src.core.settings import VectorStoreSettings
from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    VectorRecord,
    QueryResult,
    VectorStoreError,
)
from src.libs.vector_store.vector_store_factory import VectorStoreFactory


# ---------------------------------------------------------------------------
# Fake VectorStore implementation for testing
# ---------------------------------------------------------------------------

class FakeVectorStore(BaseVectorStore):
    """Fake VectorStore for testing - stores records in memory."""

    def __init__(self, settings: VectorStoreSettings):
        self.settings = settings
        self.records: dict[str, VectorRecord] = {}

    def upsert(self, records: list[VectorRecord], **kwargs) -> int:
        for record in records:
            self.records[record.id] = record
        return len(records)

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict | None = None,
        **kwargs,
    ) -> list[QueryResult]:
        results = []
        for record in self.records.values():
            # Fake similarity: just return all records with random score
            score = 0.9 - len(results) * 0.1
            results.append(QueryResult(
                id=record.id,
                score=max(0.1, score),
                text=record.text,
                metadata=record.metadata,
            ))
        return results[:top_k]

    def delete(self, ids: list[str], **kwargs) -> int:
        count = 0
        for id in ids:
            if id in self.records:
                del self.records[id]
                count += 1
        return count

    def get_by_ids(
        self, ids: list[str], **kwargs,
    ) -> list[dict]:
        # Preserve order; missing ids are skipped (matches the
        # contract documented in ``BaseVectorStore.get_by_ids``).
        return [
            {
                "id": r.id,
                "text": r.text,
                "metadata": r.metadata,
                "vector": r.vector,
            }
            for r in (self.records[i] for i in ids if i in self.records)
        ]

    def count(self, **kwargs) -> int:
        return len(self.records)


# ---------------------------------------------------------------------------
# Tests: Contract - VectorRecord shape
# ---------------------------------------------------------------------------

class TestVectorRecordContract:
    """Validate VectorRecord data shape."""

    def test_required_fields(self):
        """VectorRecord requires id, vector, text."""
        record = VectorRecord(
            id="doc-1",
            vector=[0.1, 0.2, 0.3],
            text="hello world",
        )
        assert record.id == "doc-1"
        assert record.vector == [0.1, 0.2, 0.3]
        assert record.text == "hello world"
        assert record.metadata == {}

    def test_optional_metadata(self):
        """VectorRecord supports optional metadata."""
        record = VectorRecord(
            id="doc-1",
            vector=[0.1, 0.2, 0.3],
            text="hello",
            metadata={"source": "test.txt", "page": 1},
        )
        assert record.metadata["source"] == "test.txt"

    def test_vector_is_list_of_floats(self):
        """Vector must be list of floats."""
        record = VectorRecord(id="1", vector=[0.1, 0.2], text="x")
        assert isinstance(record.vector, list)
        assert all(isinstance(v, float) for v in record.vector)


# ---------------------------------------------------------------------------
# Tests: Contract - QueryResult shape
# ---------------------------------------------------------------------------

class TestQueryResultContract:
    """Validate QueryResult data shape."""

    def test_required_fields(self):
        """QueryResult requires id, score, text."""
        result = QueryResult(
            id="doc-1",
            score=0.95,
            text="hello world",
        )
        assert result.id == "doc-1"
        assert result.score == 0.95
        assert result.text == "hello world"
        assert result.metadata == {}

    def test_score_is_float(self):
        """Score must be a float."""
        result = QueryResult(id="1", score=0.8, text="x")
        assert isinstance(result.score, float)


# ---------------------------------------------------------------------------
# Tests: Contract - BaseVectorStore interface
# ---------------------------------------------------------------------------

class TestVectorStoreContract:
    """Validate BaseVectorStore interface contract."""

    def test_base_cannot_be_instantiated(self):
        """BaseVectorStore is abstract."""
        with pytest.raises(TypeError):
            BaseVectorStore()

    def test_fake_satisfies_interface(self):
        """FakeVectorStore satisfies the interface."""
        settings = VectorStoreSettings(backend="fake")
        store = FakeVectorStore(settings)

        assert isinstance(store, BaseVectorStore)
        assert hasattr(store, "upsert")
        assert hasattr(store, "query")
        assert hasattr(store, "delete")
        assert hasattr(store, "count")


# ---------------------------------------------------------------------------
# Tests: Contract - upsert shape
# ---------------------------------------------------------------------------

class TestUpsertContract:
    """Validate upsert input/output contract."""

    def test_upsert_returns_int(self):
        """upsert() must return int (count of upserted records)."""
        settings = VectorStoreSettings(backend="fake")
        store = FakeVectorStore(settings)

        records = [
            VectorRecord(id="1", vector=[0.1], text="a"),
            VectorRecord(id="2", vector=[0.2], text="b"),
        ]
        result = store.upsert(records)

        assert isinstance(result, int)
        assert result == 2

    def test_upsert_empty_list(self):
        """upsert() handles empty input."""
        settings = VectorStoreSettings(backend="fake")
        store = FakeVectorStore(settings)

        result = store.upsert([])
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: Contract - query shape
# ---------------------------------------------------------------------------

class TestQueryContract:
    """Validate query input/output contract."""

    def test_query_returns_list_of_results(self):
        """query() must return list[QueryResult]."""
        settings = VectorStoreSettings(backend="fake")
        store = FakeVectorStore(settings)

        # First upsert some data
        store.upsert([
            VectorRecord(id="1", vector=[0.1], text="hello"),
        ])

        results = store.query(vector=[0.1], top_k=10)

        assert isinstance(results, list)
        assert all(isinstance(r, QueryResult) for r in results)

    def test_query_respects_top_k(self):
        """query() returns at most top_k results."""
        settings = VectorStoreSettings(backend="fake")
        store = FakeVectorStore(settings)

        store.upsert([
            VectorRecord(id=str(i), vector=[0.1], text=f"text-{i}")
            for i in range(5)
        ])

        results = store.query(vector=[0.1], top_k=3)
        assert len(results) <= 3

    def test_query_with_filters(self):
        """query() accepts optional filters parameter."""
        settings = VectorStoreSettings(backend="fake")
        store = FakeVectorStore(settings)

        store.upsert([
            VectorRecord(id="1", vector=[0.1], text="a", metadata={"type": "doc"}),
        ])

        # Should not raise
        results = store.query(
            vector=[0.1],
            top_k=10,
            filters={"type": "doc"},
        )
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Tests: Contract - delete shape
# ---------------------------------------------------------------------------

class TestDeleteContract:
    """Validate delete input/output contract."""

    def test_delete_returns_int(self):
        """delete() must return int (count of deleted records)."""
        settings = VectorStoreSettings(backend="fake")
        store = FakeVectorStore(settings)

        store.upsert([
            VectorRecord(id="1", vector=[0.1], text="a"),
            VectorRecord(id="2", vector=[0.2], text="b"),
        ])

        result = store.delete(["1"])
        assert isinstance(result, int)
        assert result == 1

    def test_delete_nonexistent_id(self):
        """delete() handles non-existent IDs gracefully."""
        settings = VectorStoreSettings(backend="fake")
        store = FakeVectorStore(settings)

        result = store.delete(["nonexistent"])
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: Factory routing
# ---------------------------------------------------------------------------

class TestVectorStoreFactory:
    """Test VectorStoreFactory routing logic."""

    def test_list_providers(self):
        """list_providers() returns supported backends."""
        providers = VectorStoreFactory.list_providers()
        assert isinstance(providers, list)
        assert "chroma" in providers

    def test_unsupported_backend_raises_error(self):
        """Factory raises VectorStoreError for unsupported backend."""
        settings = VectorStoreSettings(backend="nonexistent")

        with pytest.raises(VectorStoreError) as exc_info:
            VectorStoreFactory.create(settings)

        assert "nonexistent" in str(exc_info.value)

    def test_register_custom_provider(self):
        """register_provider() adds a custom backend."""
        VectorStoreFactory.register_provider(
            "fake", "tests.unit.test_vector_store_contract.FakeVectorStore"
        )

        try:
            providers = VectorStoreFactory.list_providers()
            assert "fake" in providers
        finally:
            from src.libs.vector_store.vector_store_factory import _PROVIDER_REGISTRY
            del _PROVIDER_REGISTRY["fake"]
