"""Tests for the DocReader streaming client (plan §8 / §12.2)."""

from __future__ import annotations

import pytest

from src.document_parser.base import ParserEngineInfo
from src.document_parser.client import (
    DocReaderClient,
    StreamFrame,
)
from src.document_parser.errors import ParseFailedError
from src.document_parser.types import ParseRequest, ParsedDocument


class FakeTransport:
    def __init__(self, frames, engines=None, close_calls=None) -> None:
        self._frames = list(frames)
        self._engines = engines or []
        self.close_calls = 0 if close_calls is None else close_calls

    def read_stream(self, request, timeout):
        self.last_request = request
        self.last_timeout = timeout
        return iter(self._frames)

    def list_engines(self, timeout):
        self.last_list_timeout = timeout
        return list(self._engines)

    def close(self) -> None:
        self.close_calls += 1


def _req() -> ParseRequest:
    return ParseRequest(
        source_path="/u/a.pdf", file_name="a.pdf", file_type="pdf", content=b"%PDF",
    )


def test_normal_stream_collects_meta_and_images() -> None:
    transport = FakeTransport(
        [
            StreamFrame.meta_frame(
                markdown="# doc", metadata={"engine": "builtin"}, image_count=2,
            ),
            StreamFrame.image_frame(
                filename="p1.png", original_ref="img1",
                mime_type="image/png", data=b"\x89PNG", page=3,
            ),
            StreamFrame.image_frame(
                filename="p2.png", original_ref="img2",
                mime_type="image/png", data=b"\x89PNG2",
            ),
        ],
        engines=[ParserEngineInfo(name="builtin")],
    )
    client = DocReaderClient(transport, timeout=30)
    doc: ParsedDocument = client.parse(_req())
    assert doc.markdown == "# doc"
    assert doc.metadata == {"engine": "builtin"}
    assert len(doc.images) == 2
    assert doc.images[0].page == 3
    assert doc.images[1].page is None
    assert transport.last_timeout == 30


def test_meta_error_is_failure() -> None:
    transport = FakeTransport(
        [StreamFrame.meta_frame(markdown="", error="parse exploded")],
    )
    client = DocReaderClient(transport)
    with pytest.raises(ParseFailedError) as e:
        client.parse(_req())
    assert "parse exploded" in str(e.value)


def test_image_before_meta_is_failure() -> None:
    transport = FakeTransport(
        [StreamFrame.image_frame(filename="x.png", mime_type="image/png", data=b"x")],
    )
    with pytest.raises(ParseFailedError):
        DocReaderClient(transport).parse(_req())


def test_empty_stream_is_failure() -> None:
    with pytest.raises(ParseFailedError):
        DocReaderClient(FakeTransport([])).parse(_req())


def test_list_engines_and_timeout_forwarding() -> None:
    transport = FakeTransport([], engines=[ParserEngineInfo(name="builtin")])
    client = DocReaderClient(transport, timeout=42)
    engines = client.list_engines(timeout=7)
    assert [e.name for e in engines] == ["builtin"]
    assert transport.last_list_timeout == 7


def test_close_forwarded() -> None:
    transport = FakeTransport([])
    DocReaderClient(transport).close()
    assert transport.close_calls == 1


def test_image_without_data_skipped() -> None:
    transport = FakeTransport(
        [
            StreamFrame.meta_frame(markdown="m"),
            StreamFrame.image_frame(filename="e.png", mime_type="image/png", data=b""),
        ],
    )
    doc = DocReaderClient(transport).parse(_req())
    assert doc.images == []