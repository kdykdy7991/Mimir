"""Tests for the document-parser feature flag, factory, and settings (§8 Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.settings import DocumentParserSettings, Settings
from src.document_parser.adapters import LegacyLoaderParserAdapter
from src.document_parser.client import DocReaderClient, StreamFrame
from src.document_parser.docreader_parser import DocReaderClientParser
from src.document_parser.errors import EngineUnavailableError
from src.document_parser.factory import (
    build_document_parser,
    build_document_parser_from_settings,
)
from src.document_parser.types import ParseRequest, ParsedDocument
from src.core.types import Document
from src.libs.loader.base_loader import BaseLoader


class _FakeLoader(BaseLoader):
    def load(self, path: str) -> Document:
        return Document(id="legacy1", text="from legacy", metadata={"source_path": path})

    @staticmethod
    def supported_extensions() -> frozenset[str]:
        return frozenset({".pdf"})


class _FakeTransport:
    def read_stream(self, request, timeout):
        return iter([StreamFrame.meta_frame(markdown="from docreader")])

    def list_engines(self, timeout):
        return []

    def close(self) -> None:
        pass


class TestDocumentParserSettings:
    def test_code_default_is_legacy(self) -> None:
        # Code default is legacy so programmatic/tests never attempt a
        # DocReader transport implicitly; the deployed default comes from
        # config/settings.yaml (docreader) — see test_loads_from_yaml.
        s = DocumentParserSettings()
        assert s.backend == "legacy"
        assert s.enabled is True
        assert s.endpoint == "127.0.0.1:50051"

    def test_loads_from_yaml(self) -> None:
        from src.core.settings import load_settings
        s = load_settings("config/settings.yaml")
        assert s.document_parser.backend == "docreader"
        assert s.document_parser.endpoint == "127.0.0.1:50051"

    def test_env_rollback_switch(self, monkeypatch) -> None:
        from src.core.settings import load_settings
        monkeypatch.setenv("DOCUMENT_PARSER_BACKEND", "legacy")
        s = load_settings("config/settings.yaml")
        assert s.document_parser.backend == "legacy"

    def test_rejects_unknown_backend(self) -> None:
        with pytest.raises(ValidationError):
            DocumentParserSettings(backend="cloud")

    def test_rejects_nonpositive_timeout(self) -> None:
        with pytest.raises(ValidationError):
            DocumentParserSettings(request_timeout_seconds=0)


class TestBuildDocumentParser:
    def test_legacy_backend_returns_adapter(self) -> None:
        parser = build_document_parser(
            DocumentParserSettings(backend="legacy"), loader=_FakeLoader(),
        )
        assert isinstance(parser, LegacyLoaderParserAdapter)

    def test_legacy_without_loader_raises(self) -> None:
        with pytest.raises(EngineUnavailableError):
            build_document_parser(DocumentParserSettings(backend="legacy"))

    def test_docreader_without_transport_raises(self) -> None:
        with pytest.raises(EngineUnavailableError):
            build_document_parser(
                DocumentParserSettings(backend="docreader"), loader=_FakeLoader(),
            )

    def test_docreader_with_transport_returns_client_parser(self) -> None:
        parser = build_document_parser(
            DocumentParserSettings(backend="docreader", request_timeout_seconds=9),
            transport=_FakeTransport(),
        )
        assert isinstance(parser, DocReaderClientParser)
        doc: ParsedDocument = parser.parse(
            ParseRequest(source_path="x", file_name="a.pdf", file_type="pdf", content=b"%PDF"),
        )
        assert doc.markdown == "from docreader"

    def test_disabled_falls_back_to_legacy(self) -> None:
        parser = build_document_parser(
            DocumentParserSettings(backend="docreader", enabled=False),
            loader=_FakeLoader(),
        )
        assert isinstance(parser, LegacyLoaderParserAdapter)


class TestBuildDocumentParserFromSettings:
    def test_docreader_uses_injected_grpc_transport(self) -> None:
        parser = build_document_parser_from_settings(
            DocumentParserSettings(backend="docreader", request_timeout_seconds=9),
            grpc_transport=_FakeTransport(),
        )
        assert isinstance(parser, DocReaderClientParser)
        doc = parser.parse(
            ParseRequest(source_path="x", file_name="a.pdf", file_type="pdf", content=b"%PDF"),
        )
        assert doc.markdown == "from docreader"

    def test_legacy_with_loader_returns_adapter(self) -> None:
        parser = build_document_parser_from_settings(
            DocumentParserSettings(backend="legacy"), loader=_FakeLoader(),
        )
        assert isinstance(parser, LegacyLoaderParserAdapter)

    def test_disabled_returns_legacy_adapter(self) -> None:
        parser = build_document_parser_from_settings(
            DocumentParserSettings(backend="docreader", enabled=False),
            loader=_FakeLoader(),
        )
        assert isinstance(parser, LegacyLoaderParserAdapter)