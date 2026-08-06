"""
DataService (G3) — read-only facade for the data browser page.

A thin wrapper over :class:`DocumentManager` and
:class:`ImageStorage` so the Streamlit page doesn't have to
know about either. The page just asks for a list of
documents (filtered by collection) or a single document's
detail.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Protocol

from src.ingestion.document_manager import (
    CollectionStats,
    DeleteResult,
    DocumentDetail,
    DocumentInfo,
    DocumentManager,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols — kept narrow so the page can use a fake-backed
# service in unit tests.
# ---------------------------------------------------------------------------

class _ManagerLike(Protocol):
    def list_documents(
        self, *, collection: str | None = None,
    ) -> list[DocumentInfo]: ...
    def get_document_detail(
        self, source_path: str, *, collection: str = "default",
    ) -> DocumentDetail | None: ...
    def get_collection_stats(
        self, *, collection: str = "default",
    ) -> CollectionStats: ...
    def delete_document(
        self, source_path: str, *, collection: str = "default",
    ) -> DeleteResult: ...


class _ImageStorageLike(Protocol):
    def get(self, image_id: str) -> Any | None: ...
    def find_by_doc_hash(
        self, doc_hash: str, limit: int | None = None,
    ) -> list[Any]: ...


# ---------------------------------------------------------------------------
# DataService
# ---------------------------------------------------------------------------

class DataService:
    """
    Read-only view over the document store for the dashboard.

    Usage::

        svc = DataService(manager=doc_mgr, images=img_storage)
        for info in svc.list_documents(collection="default"):
            render_row(info)
        detail = svc.get_document_detail("/a.pdf")
        image_bytes = svc.load_image_bytes("img_001")
    """

    def __init__(
        self,
        *,
        manager: _ManagerLike,
        images: _ImageStorageLike,
    ) -> None:
        self._manager = manager
        self._images = images

    # ------------------------------------------------------------------
    # List / detail (delegated)
    # ------------------------------------------------------------------
    def list_documents(
        self, *, collection: str | None = None,
    ) -> list[DocumentInfo]:
        return self._manager.list_documents(collection=collection)

    def get_document_detail(
        self, source_path: str, *, collection: str = "default",
    ) -> DocumentDetail | None:
        return self._manager.get_document_detail(
            source_path, collection=collection,
        )

    def get_collection_stats(
        self, *, collection: str = "default",
    ) -> CollectionStats:
        return self._manager.get_collection_stats(collection=collection)

    # ------------------------------------------------------------------
    # Image loading (for the data-browser preview)
    # ------------------------------------------------------------------
    def load_image_b64(self, image_id: str) -> str | None:
        """
        Read an image from ImageStorage and return it as a
        base64 string — the format ``st.image`` expects.

        Returns ``None`` if the image is missing or unreadable;
        the page renders an "image not found" chip in that
        case.
        """
        record = self._images.get(image_id)
        if record is None:
            return None
        path = getattr(record, "file_path", None)
        if path is None:
            return None
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            logger.warning("image read failed %s: %s", image_id, exc)
            return None
        return base64.b64encode(raw).decode("ascii")

    def load_image_bytes(self, image_id: str) -> bytes | None:
        """Same as :meth:`load_image_b64` but returns raw bytes."""
        record = self._images.get(image_id)
        if record is None:
            return None
        path = getattr(record, "file_path", None)
        if path is None:
            return None
        try:
            return Path(path).read_bytes()
        except OSError as exc:
            logger.warning("image read failed %s: %s", image_id, exc)
            return None


__all__ = ["DataService"]
