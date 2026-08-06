"""
``ScopedCollectionVectorStore`` — bind a :class:`MultiCollectionVectorStore`
to a single collection at construction time.

The existing ``DenseRetriever`` / ``SparseRetriever`` call
``self.vector_store.query(...)`` / ``self.vector_store.get_by_ids(...)``
without any collection parameter (their signatures predate M3). To
make a per-collection ``HybridSearch`` we want those methods to
*implicitly* target the right collection — the retriever is
constructed by ``EngineCache.hybrid_for(name)`` with a router whose
methods accept ``collection=`` kwargs.

Rather than rewriting the retrievers to thread ``collection``
through every call, we hand each retriever a tiny adapter that
forwards every method to the router with a fixed ``collection=``.
This keeps the retriever call sites untouched.
"""

from __future__ import annotations

from typing import Any


class ScopedCollectionVectorStore:
    """Forward every call to a ``MultiCollectionVectorStore`` bound to
    a single collection.

    Every method on the router is forwarded with ``collection=`` fixed
    so the legacy retrievers / upserter (whose signatures carry no
    ``collection`` kwarg) route to the right collection without their
    call sites changing:

    - ``query`` (``DenseRetriever``), ``get_by_ids`` (``SparseRetriever``)
    - ``upsert`` (``VectorUpserter`` on the ingestion path)
    - the remaining router surface for completeness
    """

    __slots__ = ("_router", "_collection")

    def __init__(self, router: Any, collection: str) -> None:
        self._router = router
        self._collection = collection

    def upsert(
        self,
        records: list[Any],
        **kwargs: Any,
    ) -> int:
        return self._router.upsert(
            records, collection=self._collection, **kwargs,
        )

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        return self._router.query(
            vector=vector,
            top_k=top_k,
            filters=filters,
            collection=self._collection,
            **kwargs,
        )

    def delete(
        self,
        ids: list[str],
        **kwargs: Any,
    ) -> int:
        return self._router.delete(
            ids, collection=self._collection, **kwargs,
        )

    def get_by_ids(
        self,
        ids: list[str],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return self._router.get_by_ids(
            ids, collection=self._collection, **kwargs,
        )

    def get_by_metadata(
        self,
        filters: dict[str, Any],
        *,
        limit: int | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return self._router.get_by_metadata(
            filters=filters, limit=limit,
            collection=self._collection, **kwargs,
        )

    def delete_by_metadata(
        self,
        filters: dict[str, Any],
        **kwargs: Any,
    ) -> int:
        return self._router.delete_by_metadata(
            filters, collection=self._collection, **kwargs,
        )

    def count(self, **kwargs: Any) -> int:
        return self._router.count(
            collection=self._collection, **kwargs,
        )

    def get_collection_stats(self, **kwargs: Any) -> dict[str, Any]:
        return self._router.get_collection_stats(
            collection=self._collection, **kwargs,
        )


__all__ = ["ScopedCollectionVectorStore"]