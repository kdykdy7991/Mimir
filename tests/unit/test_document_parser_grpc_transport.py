"""Tests for the real gRPC transport (Phase 7 runtime wiring).

Start an in-process DocReader service and drive :class:`DocReaderGrpcTransport`
through the generated stubs the same way the composition layer does, proving
the transport's frame mapping and engine listing over the wire.
"""

from __future__ import annotations

from concurrent import futures

import grpc

from docreader.main import DocReaderServicer
from docreader.parser.parser import Parser
from docreader.proto import docreader_pb2, docreader_pb2_grpc

from src.document_parser.grpc_transport import DocReaderGrpcTransport
from src.document_parser.types import ParseRequest


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