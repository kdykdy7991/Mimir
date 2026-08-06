"""
Unit tests for ``src/application/composition.py``.

Verifies the composition root wires the four application services from
settings + injectable collaborators, and that ``build_document_manager``
produces a real ``DocumentManager`` over the expected storage layout.
"""

from __future__ import annotations

import pytest

from src.application.composition import (
    ApplicationServices,
    build_application_services,
    build_document_manager,
)
from src.application.services import (
    DocumentService,
    IngestionService,
    QueryService,
    SystemService,
)
from src.core.settings import Settings
from src.ingestion.document_manager import DocumentManager


class TestBuildApplicationServices:
    def test_wires_all_four_services(self, tmp_path) -> None:
        services = build_application_services(
            data_dir=str(tmp_path),
            settings=Settings(),
            splitter=object(),
            embedding=object(),
            vector_store=object(),
        )
        assert isinstance(services, ApplicationServices)
        assert isinstance(services.query, QueryService)
        assert isinstance(services.ingestion, IngestionService)
        assert isinstance(services.document, DocumentService)
        assert isinstance(services.system, SystemService)

    def test_system_service_uses_provided_settings(self, tmp_path) -> None:
        settings = Settings()
        services = build_application_services(
            data_dir=str(tmp_path),
            settings=settings,
            splitter=object(),
            embedding=object(),
            vector_store=object(),
        )
        # SystemService reads the same settings object back.
        assert services.system.settings is settings


class TestBuildDocumentManager:
    def test_returns_manager(self, tmp_path) -> None:
        manager = build_document_manager(
            data_dir=str(tmp_path),
            settings=Settings(),
            vector_store=object(),
        )
        assert isinstance(manager, DocumentManager)

    def test_storage_paths_rooted_at_data_dir(self, tmp_path) -> None:
        manager = build_document_manager(
            data_dir=str(tmp_path),
            settings=Settings(),
            vector_store=object(),
        )
        db = tmp_path / "db"
        # Integrity checker persists under <data_dir>/db.
        assert str(db / "ingestion_history.db") == manager._integrity.db_path
        # BM25 indexer persists under <data_dir>/db/bm25.
        assert str(db / "bm25") == str(manager._bm25_indexer.persist_dir)


__all__ = []
