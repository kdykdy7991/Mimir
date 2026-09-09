"""Tests for the service-local result model and BaseParser (migrated)."""

from __future__ import annotations

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser


class FakeParser(BaseParser):
    def parse_into_text(self, content: bytes) -> Document:
        return Document(
            content=content.decode("utf-8"),
            images={"p1.png": b"\x89PNG"},
            metadata={"engine": "builtin"},
        )


def test_document_model_contract() -> None:
    doc = Document(content="hello", images={"a": b"1"}, metadata={"k": "v"})
    assert doc.content == "hello"
    assert doc.is_valid()
    doc.set_content("world")
    assert doc.get_content() == "world"
    # never serialize raw image bytes
    as_json = doc.to_json()
    assert b"\x89PNG" not in as_json.encode("utf-8") or "a" not in as_json


def test_base_parser_infers_file_type() -> None:
    # Faithful upstream behavior: extension is kept as-is (not lowercased).
    p = FakeParser(file_name="report.PDF")
    assert p.file_type == "PDF"
    p2 = FakeParser(file_name="report", file_type="explicit")
    assert p2.file_type == "explicit"


def test_base_parser_parse_flow() -> None:
    p = FakeParser(file_name="a.txt", file_type="txt")
    doc = p.parse(b"some content")
    assert doc.content == "some content"
    assert doc.images == {"p1.png": b"\x89PNG"}
    assert doc.is_valid()