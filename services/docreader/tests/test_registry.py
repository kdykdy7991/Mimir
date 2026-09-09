"""Tests for the engine registry and parser facade (Phase 1 service side)."""

from __future__ import annotations

import pytest

from docreader.models.document import Document
from docreader.parser import registry
from docreader.parser.parser import Parser, UnsupportedError


def _reset_registry():
    registry._ENGINES.clear()


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry()
    yield
    _reset_registry()


def test_registry_parses_txt() -> None:
    doc = registry.parse_file("a.txt", "txt", b"hello world")
    assert doc.content == "hello world"
    assert doc.is_valid()


def test_registry_parses_markdown() -> None:
    doc = registry.parse_file("a.md", "md", b"# title")
    assert doc.content == "# title"


def test_registry_unsupported_raises() -> None:
    with pytest.raises(ValueError):
        registry.parse_file("a.pptx", "pptx", b"PK")


def test_list_engines_advertises_builtin() -> None:
    engines = registry.list_engines()
    names = {e["name"] for e in engines}
    assert "builtin" in names
    pdf = next(e for e in engines if "pdf" in e["file_types"])
    assert pdf["available"] is True


def test_parser_facade_parse_file() -> None:
    p = Parser()
    doc = p.parse_file("a.txt", "txt", b"content", parser_engine="builtin")
    assert doc.content == "content"


def test_parser_facade_parse_url_deferred() -> None:
    p = Parser()
    with pytest.raises(UnsupportedError):
        p.parse_url("http://example.com", "t")