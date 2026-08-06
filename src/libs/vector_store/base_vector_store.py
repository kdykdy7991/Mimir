"""
VectorStore abstract base class.

Defines the unified interface for all vector database backends
(Chroma, Qdrant, Pinecone, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VectorRecord:
    """A single record for upsert into vector store."""
    id: str
    vector: list[float]
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryResult:
    """A single query result from vector store."""
    id: str
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseVectorStore(ABC):
    """
    Abstract base class for vector stores.

    All VectorStore implementations must inherit from this class
    and implement the `upsert` and `query` methods.
    """

    @abstractmethod
    def upsert(self, records: list[VectorRecord], **kwargs: Any) -> int:
        """
        Insert or update records in the vector store.

        Args:
            records: List of VectorRecord objects to upsert.
            **kwargs: Additional parameters.

        Returns:
            int: Number of records successfully upserted.

        Raises:
            VectorStoreError: If upsert fails.
        """
        pass

    @abstractmethod
    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[QueryResult]:
        """
        Query the vector store for similar vectors.

        Args:
            vector: The query vector.
            top_k: Number of results to return.
            filters: Optional metadata filters.
            **kwargs: Additional parameters.

        Returns:
            list[QueryResult]: List of matching results, sorted by score.

        Raises:
            VectorStoreError: If query fails.
        """
        pass

    @abstractmethod
    def delete(self, ids: list[str], **kwargs: Any) -> int:
        """
        Delete records from the vector store.

        Args:
            ids: List of record IDs to delete.
            **kwargs: Additional parameters.

        Returns:
            int: Number of records deleted.

        Raises:
            VectorStoreError: If delete fails.
        """
        pass

    @abstractmethod
    def get_by_ids(
        self,
        ids: list[str],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Fetch stored records by their primary key.

        Used by the sparse retriever (D3) to enrich BM25 hits with
        the full text + metadata that the vector store holds.

        Args:
            ids: List of record IDs to look up. Unknown IDs are
                simply omitted from the result; order is preserved
                (the i-th returned dict corresponds to the i-th
                found ID).
            **kwargs: Backend-specific options (e.g. ``include``).

        Returns:
            list[dict]: One dict per FOUND id, with at least
            ``id``, ``text``, and ``metadata`` keys. The list is
            shorter than ``ids`` when some are missing — callers
            should build a ``{id: record}`` map rather than rely
            on positional alignment.

        Raises:
            VectorStoreError: If the lookup fails.
        """
        pass

    def count(self, **kwargs: Any) -> int:
        """
        Get the number of records in the store.

        Args:
            **kwargs: Additional parameters.

        Returns:
            int: Number of records.
        """
        return 0


class VectorStoreError(Exception):
    """Base exception for VectorStore-related errors."""
    pass
