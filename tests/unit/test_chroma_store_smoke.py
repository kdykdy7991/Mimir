"""
Smoke tests for ChromaStore implementation.

Tests use mock to avoid loading real ChromaDB.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.settings import VectorStoreSettings
from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    QueryResult,
    VectorRecord,
    VectorStoreError,
)
from src.libs.vector_store.vector_store_factory import VectorStoreFactory


# ---------------------------------------------------------------------------
# Mock helper
# ---------------------------------------------------------------------------

def mock_chroma_client():
    """Create a mock ChromaDB client."""
    mock_client = MagicMock()
    mock_collection = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_collection
    mock_collection.count.return_value = 0
    return mock_client, mock_collection


# ---------------------------------------------------------------------------
# Tests: Factory routing
# ---------------------------------------------------------------------------

class TestProviderRouting:
    """Test that factory routes to correct provider class."""

    @patch("src.libs.vector_store.chroma_store.chromadb")
    def test_factory_routes_to_chroma(self, mock_chromadb):
        """provider=chroma creates ChromaStore."""
        settings = VectorStoreSettings(backend="chroma")
        from src.libs.vector_store.vector_store_factory import VectorStoreFactory
        store = VectorStoreFactory.create(settings)
        assert store.__class__.__name__ == "ChromaStore"


# ---------------------------------------------------------------------------
# Tests: ChromaStore
# ---------------------------------------------------------------------------

class TestChromaStore:
    """Test ChromaStore with mock."""

    @patch("src.libs.vector_store.chroma_store.chromadb")
    def test_upsert_returns_count(self, mock_chromadb):
        """upsert() returns count of upserted records."""
        mock_client, mock_collection = mock_chroma_client()
        mock_chromadb.PersistentClient.return_value = mock_client

        settings = VectorStoreSettings(backend="chroma", persist_path="/tmp/test")
        from src.libs.vector_store.chroma_store import ChromaStore
        store = ChromaStore(settings)

        records = [
            VectorRecord(id="1", vector=[0.1, 0.2], text="hello"),
            VectorRecord(id="2", vector=[0.3, 0.4], text="world"),
        ]
        result = store.upsert(records)
        assert result == 2

    @patch("src.libs.vector_store.chroma_store.chromadb")
    def test_upsert_empty_list(self, mock_chromadb):
        """upsert() handles empty input."""
        mock_client, mock_collection = mock_chroma_client()
        mock_chromadb.PersistentClient.return_value = mock_client

        settings = VectorStoreSettings(backend="chroma", persist_path="/tmp/test")
        from src.libs.vector_store.chroma_store import ChromaStore
        store = ChromaStore(settings)

        result = store.upsert([])
        assert result == 0
        mock_collection.upsert.assert_not_called()

    @patch("src.libs.vector_store.chroma_store.chromadb")
    def test_query_returns_results(self, mock_chromadb):
        """query() returns list of QueryResult."""
        mock_client, mock_collection = mock_chroma_client()
        mock_chromadb.PersistentClient.return_value = mock_client

        # Mock query response
        mock_collection.query.return_value = {
            "ids": [["doc1", "doc2"]],
            "documents": [["text1", "text2"]],
            "metadatas": [[{"source": "a"}, {"source": "b"}]],
            "distances": [[0.1, 0.3]],
        }

        settings = VectorStoreSettings(backend="chroma", persist_path="/tmp/test")
        from src.libs.vector_store.chroma_store import ChromaStore
        store = ChromaStore(settings)

        results = store.query(vector=[0.1, 0.2], top_k=2)
        assert isinstance(results, list)
        assert len(results) == 2
        assert all(isinstance(r, QueryResult) for r in results)

    @patch("src.libs.vector_store.chroma_store.chromadb")
    def test_query_with_filters(self, mock_chromadb):
        """query() passes filters to ChromaDB."""
        mock_client, mock_collection = mock_chroma_client()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_collection.query.return_value = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        settings = VectorStoreSettings(backend="chroma", persist_path="/tmp/test")
        from src.libs.vector_store.chroma_store import ChromaStore
        store = ChromaStore(settings)

        store.query(vector=[0.1], top_k=5, filters={"type": "doc"})
        call_kwargs = mock_collection.query.call_args.kwargs
        assert call_kwargs["where"] == {"type": "doc"}

    @patch("src.libs.vector_store.chroma_store.chromadb")
    def test_delete_returns_count(self, mock_chromadb):
        """delete() returns count of deleted records."""
        mock_client, mock_collection = mock_chroma_client()
        mock_chromadb.PersistentClient.return_value = mock_client

        settings = VectorStoreSettings(backend="chroma", persist_path="/tmp/test")
        from src.libs.vector_store.chroma_store import ChromaStore
        store = ChromaStore(settings)

        result = store.delete(["id1", "id2", "id3"])
        assert result == 3

    @patch("src.libs.vector_store.chroma_store.chromadb")
    def test_delete_empty_list(self, mock_chromadb):
        """delete() handles empty input."""
        mock_client, mock_collection = mock_chroma_client()
        mock_chromadb.PersistentClient.return_value = mock_client

        settings = VectorStoreSettings(backend="chroma", persist_path="/tmp/test")
        from src.libs.vector_store.chroma_store import ChromaStore
        store = ChromaStore(settings)

        result = store.delete([])
        assert result == 0
        mock_collection.delete.assert_not_called()

    @patch("src.libs.vector_store.chroma_store.chromadb")
    def test_count_returns_number(self, mock_chromadb):
        """count() returns number of records."""
        mock_client, mock_collection = mock_chroma_client()
        mock_chromadb.PersistentClient.return_value = mock_client
        mock_collection.count.return_value = 42

        settings = VectorStoreSettings(backend="chroma", persist_path="/tmp/test")
        from src.libs.vector_store.chroma_store import ChromaStore
        store = ChromaStore(settings)

        assert store.count() == 42


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Test error handling for ChromaStore."""

    @patch("src.libs.vector_store.chroma_store.chromadb", None)
    def test_chromadb_not_installed(self):
        """Raises error when chromadb not installed."""
        settings = VectorStoreSettings(backend="chroma")
        from src.libs.vector_store.chroma_store import ChromaStore

        with pytest.raises(VectorStoreError) as exc_info:
            ChromaStore(settings)
        assert "not installed" in str(exc_info.value)
