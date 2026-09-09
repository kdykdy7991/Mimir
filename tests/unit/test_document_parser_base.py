"""Tests for the DocumentParser protocol & engine-info model (plan §5.3)."""

from __future__ import annotations

from src.document_parser.base import DocumentParser, ParserEngineInfo
from src.document_parser.types import ParseRequest, ParsedDocument


class _ConformingParser:
    """A structurally-conforming DocumentParser for runtime_checkable tests."""

    def parse(self, request: ParseRequest) -> ParsedDocument:
        return ParsedDocument(markdown=f"parsed {request.file_name}")

    def list_engines(self) -> list[ParserEngineInfo]:
        return [ParserEngineInfo(name="builtin", supported_formats=("pdf",))]


def test_parser_engine_info_defaults() -> None:
    info = ParserEngineInfo(name="builtin")
    assert info.supported_formats == ()
    assert info.available is True
    assert info.unavailable_reason is None
    assert info.extra == {}


def test_parser_engine_info_unavailable() -> None:
    info = ParserEngineInfo(
        name="opendataloader", available=False,
        unavailable_reason="not installed",
    )
    assert not info.available
    assert info.unavailable_reason == "not installed"


def test_runtime_checkable_protocol() -> None:
    assert isinstance(_ConformingParser(), DocumentParser)


def test_protocol_rejects_non_conforming() -> None:
    class Bad:
        def parse(self, request: ParseRequest) -> ParsedDocument:
            return ParsedDocument(markdown="")

    # runtime_checkable Protocol checks for method presence only.
    assert not isinstance(Bad(), DocumentParser)


def test_conforming_parser_roundtrip() -> None:
    parser = _ConformingParser()
    req = ParseRequest(
        source_path="/tmp/x.pdf", file_name="x.pdf", file_type="pdf", content=b"%PDF",
    )
    doc = parser.parse(req)
    assert doc.markdown == "parsed x.pdf"
    assert [e.name for e in parser.list_engines()] == ["builtin"]