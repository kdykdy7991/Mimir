"""Tests for the gRPC DocReader servicer (streaming ReadStream, engines).

Handlers are invoked directly (no real gRPC channel needed): a fake Parser is
injected so behaviour is deterministic.
"""

from __future__ import annotations

import pytest

from docreader.main import DocReaderServicer
from docreader.models.document import Document
from docreader.parser.parser import Parser
from docreader.proto import docreader_pb2


class FakeParser(Parser):
    def parse_file(self, file_name, file_type, content, **kw):
        return Document(
            content=content.decode("utf-8"),
            images={"assets/p1.png": b"\x89PNG-fake"},
            metadata={"engine": "builtin"},
        )


def _read_request() -> docreader_pb2.ReadRequest:
    return docreader_pb2.ReadRequest(
        file_content=b"hello # md",
        file_name="a.md",
        file_type="md",
        config=docreader_pb2.ReadConfig(parser_engine="builtin"),
        request_id="req-1",
    )


class _Ctx:
    pass


def test_readstream_meta_then_image() -> None:
    service = DocReaderServicer(parser=FakeParser())
    frames = list(service.ReadStream(_read_request(), _Ctx()))
    assert len(frames) == 2
    meta = frames[0].meta
    assert meta.markdown_content == "hello # md"
    assert meta.image_count == 1
    assert meta.metadata["engine"] == "builtin"
    img = frames[1].image
    assert img.image_data == b"\x89PNG-fake"
    assert img.mime_type == "image/png"


def test_readstream_error_meta_on_failure() -> None:
    class Boom(FakeParser):
        def parse_file(self, *a, **k):
            raise RuntimeError("boom")

    frames = list(DocReaderServicer(parser=Boom()).ReadStream(_read_request(), _Ctx()))
    assert len(frames) == 1
    assert "boom" in frames[0].meta.error


def test_read_unary_returns_markdown() -> None:
    resp = DocReaderServicer(parser=FakeParser()).Read(_read_request(), _Ctx())
    assert resp.markdown_content == "hello # md"
    assert len(resp.image_refs) == 1


def test_list_engines_returns_builtin() -> None:
    resp = DocReaderServicer().ListEngines(docreader_pb2.ListEnginesRequest(), _Ctx())
    names = [e.name for e in resp.engines]
    assert "builtin" in names


def test_config_fails_fast_on_invalid_port(monkeypatch) -> None:
    from docreader.config import DocReaderConfig
    monkeypatch.setenv("DOCREADER_GRPC_PORT", "70000")
    with pytest.raises(ValueError):
        DocReaderConfig.from_env()
    monkeypatch.delenv("DOCREADER_GRPC_PORT")
    cfg = DocReaderConfig.from_env()
    assert cfg.grpc_port == 50051