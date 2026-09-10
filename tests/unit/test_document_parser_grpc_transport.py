"""Tests for the real gRPC transport (Phase 7 runtime wiring).

Start an in-process DocReader service and drive :class:`DocReaderGrpcTransport`
through the generated stubs the same way the composition layer does, proving
the transport's frame mapping and engine listing over the wire.
"""

from __future__ import annotations

from concurrent import futures

import grpc
import pytest

# The docreader service package is a separate deployable (importable when
# services/docreader is on ``sys.path``). Skip cleanly instead of erroring when
# running the main ``tests/unit`` suite without it.
docreader_main = pytest.importorskip("docreader.main")
docreader_parser = pytest.importorskip("docreader.parser.parser")
docreader_proto = pytest.importorskip("docreader.proto")

from docreader.main import DocReaderServicer  # noqa: E402
from docreader.parser.parser import Parser  # noqa: E402
from docreader.proto import docreader_pb2, docreader_pb2_grpc  # noqa: E402

from src.document_parser.errors import EngineUnavailableError  # noqa: E402
from src.document_parser.grpc_transport import DocReaderGrpcTransport  # noqa: E402
from src.document_parser.types import ParseRequest  # noqa: E402


def _start() -> tuple[grpc.Server, str]:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    docreader_pb2_grpc.add_DocReaderServicer_to_server(DocReaderServicer(parser=Parser()), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def test_grpc_transport_reads_stream_and_lists_engines() -> None:
    server, port = _start()
    try:
        transport = DocReaderGrpcTransport(f"127.0.0.1:{port}")
        try:
            engines = transport.list_engines(timeout=10)
            assert {e.name for e in engines} == {"builtin", "opendataloader"}
            frames = list(transport.read_stream(
                ParseRequest(source_path="g.md", file_name="g.md", file_type="md", content=b"# md"),
                timeout=20,
            ))
            assert frames and frames[0].meta is not None
            assert frames[0].meta.error is None
            assert "# md" in frames[0].meta.markdown
        finally:
            transport.close()
    finally:
        server.stop(0)


def test_grpc_transport_reports_connection_error_when_down() -> None:
    # A port with no listener → gRPC channel is unreachable → ConnectionError.
    transport = DocReaderGrpcTransport("127.0.0.1:1", timeout=1)
    try:
        try:
            list(transport.read_stream(
                ParseRequest(source_path="g.md", file_name="g.md", file_type="md", content=b"# md"),
                timeout=1,
            ))
        except ConnectionError:
            pass
        else:
            raise AssertionError("expected ConnectionError for unreachable endpoint")
    finally:
        transport.close()


def test_grpc_transport_probe_ok_against_live_server() -> None:
    # Startup fail-fast: with a live server, probe() performs a real round-trip
    # and returns the engine list (builtin covers MIN_READY_FORMATS).
    server, port = _start()
    try:
        transport = DocReaderGrpcTransport(f"127.0.0.1:{port}")
        try:
            engines = transport.probe(timeout=10)
            names = {e.name for e in engines}
            assert "builtin" in names
            builtin = next(e for e in engines if e.name == "builtin")
            from src.document_parser.grpc_transport import MIN_READY_FORMATS
            assert MIN_READY_FORMATS <= frozenset(builtin.supported_formats)
        finally:
            transport.close()
    finally:
        server.stop(0)


def test_grpc_transport_probe_raises_when_down() -> None:
    # Startup fail-fast: a dead endpoint must raise EngineUnavailableError at
    # probe time (not silently defer to lazy channel / first parse).
    transport = DocReaderGrpcTransport("127.0.0.1:1", timeout=1)
    try:
        try:
            transport.probe(timeout=1)
        except EngineUnavailableError:
            pass
        else:
            raise AssertionError("expected EngineUnavailableError for dead endpoint")
    finally:
        transport.close()