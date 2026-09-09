"""Tests for the chain (FirstParser / PipelineParser) — adapted from
WeKnora ``docreader/parser/chain_parser.py`` semantics, including the required
"don't swallow the final error" adaptation (plan §6)."""

from __future__ import annotations

import pytest

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.chain_parser import ChainParseError, FirstParser, PipelineParser


class OkParser(BaseParser):
    def parse_into_text(self, content: bytes) -> Document:
        return Document(content=f"ok:{content!r}", metadata={"engine": "ok"})


class FailParser(BaseParser):
    def parse_into_text(self, content: bytes) -> Document:
        raise RuntimeError("boom")


class EmptyParser(BaseParser):
    def parse_into_text(self, content: bytes) -> Document:
        return Document()


def test_first_parser_uses_first_success() -> None:
    Parser = FirstParser.create(FailParser, OkParser, EmptyParser)
    doc = Parser().parse_into_text(b"data")
    assert "ok" in doc.content
    assert doc.metadata == {"engine": "ok"}


def test_first_parser_raises_on_total_failure_with_attempts() -> None:
    Parser = FirstParser.create(FailParser, EmptyParser)
    with pytest.raises(ChainParseError) as e:
        Parser().parse_into_text(b"x")
    # both engines recorded, none ok
    assert all(not a["ok"] for a in e.value.attempts)
    assert len(e.value.attempts) == 2


def test_first_parser_create_synthesizes_subclass() -> None:
    Parser = FirstParser.create(OkParser)
    assert issubclass(Parser, FirstParser)


def test_pipeline_accumulates_images_and_metadata() -> None:
    class AddImg(BaseParser):
        def parse_into_text(self, content: bytes) -> Document:
            return Document(
                content="acc",
                images={"a": b"1"},
                metadata={"m": "v"},
            )

    Parser = PipelineParser.create(AddImg, AddImg)
    doc = Parser().parse_into_text(b"seed")
    assert doc.content == "acc"
    assert doc.images == {"a": b"1"}
    assert doc.metadata == {"m": "v"}