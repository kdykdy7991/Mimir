"""
Unit tests for ``DocumentService`` (M1 application layer).

Thin facade over ``DocumentManager`` lifecycle ops — verified with a
recording fake; cross-store deletion semantics live in
``tests/unit/test_document_manager.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.application.services import DocumentService


@dataclass
class _FakeInfo:
    source_path: str
    collection: str = "default"
    n_chunks: int = 0
    n_images: int = 0


@dataclass
class _FakeDetail:
    source_path: str
    collection: str = "default"


@dataclass
class _FakeDeleteResult:
    source_path: str
    total: int = 0


@dataclass
class _FakeStats:
    collection: str = "default"
    n_documents: int = 0
    n_chunks: int = 0
    n_images: int = 0


class _FakeManager:
    """Records delegation; returns canned values."""

    def __init__(self) -> None:
        self.calls: list = []
        self._stats = _FakeStats(n_documents=3)

    def list_documents(self, *, collection: str | None = None) -> list:
        self.calls.append(("list", {"collection": collection}))
        return [
            _FakeInfo("/a.pdf", collection=collection or "default"),
            _FakeInfo("/b.pdf", collection=collection or "default"),
        ]

    def get_document_detail(
        self, *, source_path: str, collection: str = "default",
    ) -> _FakeDetail:
        self.calls.append(("detail", {"source_path": source_path, "collection": collection}))
        return _FakeDetail(source_path, collection)

    def delete_document(
        self, *, source_path: str, collection: str = "default",
    ) -> _FakeDeleteResult:
        self.calls.append(("delete", {"source_path": source_path, "collection": collection}))
        return _FakeDeleteResult(source_path)

    def get_collection_stats(self, *, collection: str = "default") -> _FakeStats:
        self.calls.append(("stats", {"collection": collection}))
        return self._stats


@pytest.fixture
def manager() -> _FakeManager:
    return _FakeManager()


@pytest.fixture
def svc(manager: _FakeManager) -> DocumentService:
    return DocumentService(manager)


class TestConstruction:
    def test_exposes_collaborator(self, manager: _FakeManager) -> None:
        assert DocumentService(manager).manager is manager


class TestListDocuments:
    def test_delegates(self, svc: DocumentService, manager: _FakeManager) -> None:
        docs = svc.list_documents(collection="reports")
        assert [d.source_path for d in docs] == ["/a.pdf", "/b.pdf"]
        assert manager.calls[0] == ("list", {"collection": "reports"})

    def test_defaults_to_none_collection(
        self, svc: DocumentService, manager: _FakeManager,
    ) -> None:
        svc.list_documents()
        assert manager.calls[0] == ("list", {"collection": None})


class TestGetDocumentDetail:
    def test_delegates(self, svc: DocumentService, manager: _FakeManager) -> None:
        detail = svc.get_document_detail("/a.pdf", "default")
        assert detail.source_path == "/a.pdf"
        assert manager.calls[0] == (
            "detail", {"source_path": "/a.pdf", "collection": "default"},
        )


class TestDeleteDocument:
    def test_delegates(self, svc: DocumentService, manager: _FakeManager) -> None:
        result = svc.delete_document("/a.pdf", "default")
        assert result.source_path == "/a.pdf"
        assert manager.calls[0] == (
            "delete", {"source_path": "/a.pdf", "collection": "default"},
        )


class TestGetCollectionStats:
    def test_delegates(self, svc: DocumentService, manager: _FakeManager) -> None:
        stats = svc.get_collection_stats(collection="default")
        assert stats.n_documents == 3
        assert manager.calls[0] == ("stats", {"collection": "default"})


__all__ = []
