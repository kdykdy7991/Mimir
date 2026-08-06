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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ingestion.document_manager import (
        CollectionRef,
        CollectionStats,
        DeleteResult,
        DocumentDetail,
        DocumentInfo,
        DocumentManager,
    )

logger = logging.getLogger(__name__)


class DocumentService:
    """Thin facade over :class:`DocumentManager` for M1.

    Constructed once at app boot with the pre-built manager.
    """

    def __init__(self, manager: "DocumentManager") -> None:
        self._manager = manager

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

    def get_document_detail(
        self, source_path: str, collection: str,
    ) -> "DocumentDetail":
        return self._manager.get_document_detail(
            source_path=source_path, collection=collection,
        )

    def delete_document(
        self, source_path: str, collection: str,
    ) -> "DeleteResult":
        return self._manager.delete_document(
            source_path=source_path, collection=collection,
        )

    def get_collection_stats(
        self, collection: str | None = None,
    ) -> "CollectionStats":
        return self._manager.get_collection_stats(collection=collection)

    # ------------------------------------------------------------------
    # Collections (knowledge-base level) — M2 Web API
    # ------------------------------------------------------------------
    def list_collections(self) -> list["CollectionRef"]:
        return self._manager.list_collections()

    def create_collection(self, name: str) -> None:
        self._manager.create_collection(name)

    def delete_collection(self, name: str) -> list["DeleteResult"]:
        return self._manager.delete_collection(name)


__all__ = ["DocumentService"]
