"""
``MultiCollectionVectorStore`` — a router that holds one
:class:`ChromaStore` per knowledge-base collection.

Why this exists
---------------
``ChromaStore`` binds a single ChromaDB ``Collection`` handle at
``__init__`` time (see ``chroma_store.py:64-83``). The M3
multi-collection feature needs every collection to live in its own
Chroma collection; we want to reuse the same ``chromadb.PersistentClient``
across all of them (one client handles many collections cheaply).

Rather than reworking ``ChromaStore`` itself — which would touch the
existing call graph and the unit tests — this module adds a thin
``BaseVectorStore`` subclass that dispatches every call to the
correct per-collection sub-store.

Construction
------------
``MultiCollectionVectorStore`` takes the same
``VectorStoreSettings`` instance as ``ChromaStore`` (so the
underlying persist_path / client options stay consistent), then
lazily constructs ``ChromaStore`` instances on first access. The
``VectorStoreFactory.create_multi_collection`` entry point is the
preferred builder.
"""

from __future__ import annotations

import logging
from typing import Any

from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    QueryResult,
    VectorRecord,
    VectorStoreError,
)
from src.libs.vector_store.chroma_store import ChromaStore, chroma_collection_name

logger = logging.getLogger(__name__)


class MultiCollectionVectorStore(BaseVectorStore):
    """Routes every call to the ``ChromaStore`` for ``collection``.

    Holds one underlying ``chromadb.PersistentClient`` (constructed
    via the settings object) and one ``ChromaStore`` per accessed
    collection. New collections are constructed lazily on first
    ``upsert`` / ``query`` / etc. call; you can also prime the cache
    by calling :meth:`prime` after creating the Web API app.
    """

    def __init__(self, settings: Any) -> None:
        # Mirror ChromaStore.__init__'s surface — same persist
        # path + same defaults — but defer the ``get_or_create_collection``
        # call until we know which collection is being touched.
        self._settings = settings
        self._persist_directory = getattr(
            settings, "persist_path", "./data/db/chroma",
        )
        self._client = self._make_client(self._persist_directory)
        self._stores: dict[str, ChromaStore] = {}
        # Lock guards the lazy ``_stores[name]`` allocation. The
        # underlying ChromaStore is itself thread-safe (the inner
        # ``self._collection`` handle is built once); we just need
        # to avoid two threads racing on the dict insert.
        self._lock = __import__("threading").Lock()

    # ------------------------------------------------------------------
    # Internal — client + sub-store plumbing
    # ------------------------------------------------------------------
    @staticmethod
    def _make_client(persist_directory: str):
        """Build the shared persistent Chroma client.

        Mirrors ``ChromaStore.__init__``'s client construction so
        behaviour is identical to the legacy single-collection
        path. Raises ``VectorStoreError`` if chromadb isn't installed
        (the legacy code does the same).
        """
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
        except ImportError as exc:  # pragma: no cover — exercised
            # by the ChromaDB dependency check at factory level.
            raise VectorStoreError(
                "chromadb package is not installed. "
                "Install it with: pip install chromadb",
            ) from exc
        return chromadb.PersistentClient(
            path=persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    def _store_for(self, collection: str) -> ChromaStore:
        """Return (lazily constructing) the ``ChromaStore`` for ``collection``."""
        with self._lock:
            store = self._stores.get(collection)
            if store is not None:
                return store
            store = self._build_store(collection)
            self._stores[collection] = store
            return store

    def _build_store(self, collection: str) -> ChromaStore:
        """Build a ``ChromaStore``-equivalent bound to a single collection.

        Avoids touching ``ChromaStore.__init__`` (which would build
        its own client). We replicate the bits we need: settings
        mutated to point at this collection's name + the persistent
        client handle.
        """
        store = ChromaStore.__new__(ChromaStore)
        store.settings = self._settings
        # ``_collection_name`` keeps the display name (stats / DocumentManager
        # read it); the Chroma handle binds the mapped Chroma-safe name so a
        # Chinese-named knowledge base works (see chroma_collection_name).
        store._collection_name = collection
        store._client = self._client
        store._collection = self._client.get_or_create_collection(
            name=chroma_collection_name(collection),
        )
        return store

    def prime(self, collections: list[str]) -> None:
        """Eagerly construct the ``ChromaStore`` for each given collection.

        Optional — the router builds stores lazily anyway. Useful at
        app boot to surface ChromaDB errors up front rather than on
        the first query / upload.
        """
        for name in collections:
            self._store_for(name)

    def heartbeat(self) -> dict:
        """Probe the underlying ChromaDB client (health check).

        Returns chromadb's heartbeat payload (server-relative latency in
        ms). Raises if the persistent client is unreachable — the health
        endpoint maps that to ``down``.
        """
        return self._client.heartbeat()

    # ------------------------------------------------------------------
    # BaseVectorStore surface — every method takes ``collection``
    # ------------------------------------------------------------------
    def upsert(
        self,
        records: list[VectorRecord],
        *,
        collection: str = "default",
        **kwargs: Any,
    ) -> int:
        return self._store_for(collection).upsert(records, **kwargs)

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        *,
        collection: str = "default",
        **kwargs: Any,
    ) -> list[QueryResult]:
        return self._store_for(collection).query(
            vector=vector, top_k=top_k, filters=filters, **kwargs,
        )

    def delete(
        self,
        ids: list[str],
        *,
        collection: str = "default",
        **kwargs: Any,
    ) -> int:
        return self._store_for(collection).delete(ids, **kwargs)

    def get_by_ids(
        self,
        ids: list[str],
        *,
        collection: str = "default",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return self._store_for(collection).get_by_ids(ids, **kwargs)

    def get_by_metadata(
        self,
        filters: dict[str, Any],
        *,
        limit: int | None = None,
        collection: str = "default",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return self._store_for(collection).get_by_metadata(
            filters=filters, limit=limit, **kwargs,
        )

    def delete_by_metadata(
        self,
        filters: dict[str, Any],
        *,
        collection: str = "default",
        **kwargs: Any,
    ) -> int:
        return self._store_for(collection).delete_by_metadata(
            filters=filters, **kwargs,
        )

    def count(
        self, *, collection: str = "default", **kwargs: Any,
    ) -> int:
        return self._store_for(collection).count(**kwargs)

    def get_collection_stats(
        self, *, collection: str = "default", **kwargs: Any,
    ) -> dict[str, Any]:
        return self._store_for(collection).get_collection_stats(**kwargs)


__all__ = ["MultiCollectionVectorStore"]