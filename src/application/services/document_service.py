"""
``DocumentService`` — application-layer entry point for cross-store document
lifecycle operations.

M1 职责（v0.1 契约）

- 薄封装，**不改变** ``DocumentManager`` 的行为。
- 给 CLI / MCP / Streamlit / Web API 四个入口一个稳定的依赖项。
- 现有 ``DocumentInfo`` / ``DocumentDetail`` / ``DeleteResult`` / ``CollectionStats``
  数据类继续作为返回类型，不引入新 DTO。

后续会扩展

- SQLite 持久化（M2，Web API 落库时）
- Web API DTO 转换（M2）
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING
from uuid import UUID

from src.application.identifiers import document_uuid

if TYPE_CHECKING:
    from src.ingestion.document_manager import (
        CollectionRef,
        CollectionStats,
        CorpusOverviewStats,
        DeleteResult,
        DocumentDetail,
        DocumentInfo,
        DocumentManager,
    )

logger = logging.getLogger(__name__)

# How long the doc-UUID index is considered fresh after a (re)build. New
# documents appear asynchronously via the ingestion pipeline, so a short
# TTL + a rebuild-on-miss lets a just-ingested doc resolve without an
# always-full scan while still bounding recompute cost.
_DOC_INDEX_TTL_SECONDS = 15.0

# How long the batch collection-stats aggregate stays cached before being
# recomputed. Kept short so a just-ingested document shows up quickly.
_STATS_CACHE_TTL_SECONDS = 10.0


class DocumentService:
    """Thin facade over :class:`DocumentManager` for M1.

    Constructed once at app boot with the pre-built manager.
    """

    def __init__(self, manager: "DocumentManager") -> None:
        self._manager = manager
        # Cached index: document UUID -> (collection, source_path).
        self._doc_index: dict[UUID, tuple[str, str]] | None = None
        self._doc_index_ts = 0.0
        self._doc_index_lock = threading.Lock()
        # Cached batch-collection-stats (list aggregate). Mutations below
        # invalidate it; a short TTL bounds how long a document ingested
        # via the background pipeline is stale.
        self._stats_cache: dict[str, "CollectionStats"] | None = None
        self._stats_cache_key: frozenset[str] = frozenset()
        self._stats_cache_ts = 0.0

    @property
    def manager(self) -> "DocumentManager":
        return self._manager

    @property
    def image_storage(self):
        """The document manager's image storage (Web API batch 3)."""
        return self._manager.image_storage

    def list_documents(
        self, collection: str | None = None,
    ) -> list["DocumentInfo"]:
        return self._manager.list_documents(collection=collection)

    def list_documents_paged(
        self, collection: str | None = None, offset: int = 0,
        limit: int = 20,
        status: str | None = None,
        q: str | None = None, file_type: str | None = None,
        updated_after: float | None = None, updated_before: float | None = None,
        sort: str | None = None,
        source_paths_include: list[str] | None = None,
    ) -> tuple[list["DocumentInfo"], int]:
        """Server-side paged listing (see manager for the cheap path).

        B2.5 filter predicates are forwarded to the store and applied in
        SQL (``status`` / ``q`` / ``file_type`` / date-range / ``sort`` /
        ``source_paths_include``).
        """
        return self._manager.list_documents_paged(
            collection=collection, offset=offset, limit=limit,
            status=status, q=q, file_type=file_type,
            updated_after=updated_after, updated_before=updated_before,
            sort=sort, source_paths_include=source_paths_include,
        )

    def get_document_detail(
        self, source_path: str, collection: str,
    ) -> "DocumentDetail":
        return self._manager.get_document_detail(
            source_path=source_path, collection=collection,
        )

    def delete_document(
        self, source_path: str, collection: str,
    ) -> "DeleteResult":
        self.invalidate_doc_index()
        self.invalidate_collection_stats()
        return self._manager.delete_document(
            source_path=source_path, collection=collection,
        )

    def get_collection_stats(
        self, collection: str | None = None,
    ) -> "CollectionStats":
        return self._manager.get_collection_stats(collection=collection)

    def get_all_collection_stats(
        self, collections: list[str],
    ) -> dict[str, "CollectionStats"]:
        """Per-collection summary cards in one aggregated pass."""
        return self._manager.get_all_collection_stats(
            collections=collections,
        )

    def get_all_collection_stats_cached(
        self, collections: list[str],
    ) -> dict[str, "CollectionStats"]:
        """Batch stats per collection, cached briefly for list endpoints.

        Invalidated on document / collection mutations (see
        :meth:`invalidate_collection_stats`); a short TTL also bounds
        staleness for documents ingested via the background pipeline.
        """
        key = frozenset(collections)
        now = time.monotonic()
        if (
            self._stats_cache is not None
            and self._stats_cache_key == key
            and (now - self._stats_cache_ts) < _STATS_CACHE_TTL_SECONDS
        ):
            return self._stats_cache
        fresh = self.get_all_collection_stats(collections)
        self._stats_cache = fresh
        self._stats_cache_key = key
        self._stats_cache_ts = time.monotonic()
        return fresh

    def invalidate_collection_stats(self) -> None:
        """Drop the cached batch-collection-stats aggregate."""
        self._stats_cache = None
        self._stats_cache_key = frozenset()
        self._stats_cache_ts = 0.0

    def invalidate_ingest_caches(self) -> None:
        """Invalidate both caches after a document is ingested/removed.

        Wired as the ingestion worker's ``on_ingested`` hook so listing
        aggregates and the document-UUID index reflect new documents
        immediately after the pipeline finishes writing its integrity
        record (instead of waiting out the short TTLs).
        """
        self.invalidate_doc_index()
        self.invalidate_collection_stats()

    def get_corpus_overview_stats(
        self, collections: list[str],
    ) -> "CorpusOverviewStats":
        """Return corpus totals through the manager's aggregate read path."""
        return self._manager.get_corpus_overview_stats(
            collections=collections,
        )

    # ------------------------------------------------------------------
    # Collections (knowledge-base level) — M2 Web API
    # ------------------------------------------------------------------
    def list_collections(self) -> list["CollectionRef"]:
        return self._manager.list_collections()

    def create_collection(self, name: str) -> None:
        self._manager.create_collection(name)
        self.invalidate_collection_stats()

    def delete_collection(self, name: str) -> list["DeleteResult"]:
        self.invalidate_doc_index()
        self.invalidate_collection_stats()
        return self._manager.delete_collection(name)

    # ------------------------------------------------------------------
    # Document-UUID index (avoids a full N+1 scan on /documents/{id})
    # ------------------------------------------------------------------
    def invalidate_doc_index(self) -> None:
        """Drop the cached UUID→(collection, source_path) index.

        Called after document / collection mutations so a following
        lookup reflects the new state.
        """
        with self._doc_index_lock:
            self._doc_index = None
            self._doc_index_ts = 0.0

    def resolve_document_id(self, document_id: UUID) -> tuple[str, str] | None:
        """Map a stable document UUID back to ``(collection, source_path)``.

        Returns ``None`` when no known document matches. Backed by a
        short-TTL index built from a single integrity listing (no per-doc
        Chroma / image lookups).

        A fresh-cache **miss** returns ``None`` directly — it does **not**
        force a rebuild, so flooding with nonexistent UUIDs can't turn this
        into a repeated O(N) scan. The index is rebuilt only when its TTL
        has elapsed or it was explicitly invalidated (deletes, or the
        ``on_ingested`` hook after a successful upload).
        """
        index = self._cached_doc_index()
        return index.get(document_id)

    def _cached_doc_index(self) -> dict[UUID, tuple[str, str]]:
        with self._doc_index_lock:
            if (
                self._doc_index is not None
                and (time.monotonic() - self._doc_index_ts) < _DOC_INDEX_TTL_SECONDS
            ):
                return self._doc_index
        return self._rebuild_doc_index()

    def _rebuild_doc_index(self) -> dict[UUID, tuple[str, str]]:
        index: dict[UUID, tuple[str, str]] = {}
        for collection, source_path in self._manager.list_document_keys():
            index[document_uuid(collection, source_path)] = (
                collection, source_path,
            )
        with self._doc_index_lock:
            self._doc_index = index
            self._doc_index_ts = time.monotonic()
        return index


__all__ = ["DocumentService"]
