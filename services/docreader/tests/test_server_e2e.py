"""End-to-end gRPC test: a real server serving ReadStream / ListEngines over
the wire using the generated stubs. Verifies the serving path, not just the
handlers.
"""

from __future__ import annotations

from concurrent import futures

import grpc

from docreader.main import DocReaderServicer
from docreader.parser.parser import Parser
from docreader.proto import docreader_pb2, docreader_pb2_grpc


def _plain_servicer_parser() -> Parser:
    # Real registry path: plain text/markdown passthrough.
    return Parser()


def test_readstream_over_wire() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    docreader_pb2_grpc.add_DocReaderServicer_to_server(
        DocReaderServicer(parser=_plain_servicer_parser()), server,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = docreader_pb2_grpc.DocReaderStub(channel)
            frames = list(
                stub.ReadStream(
                    docreader_pb2.ReadRequest(
                        file_content=b"# hi from wire\nbody",
                        file_name="a.md",
                        file_type="md",
                    ),
                    timeout=10,
                ),
            )
    finally:
        server.stop(0)

    assert len(frames) == 1
    meta = frames[0].meta
    assert meta.markdown_content == "# hi from wire\nbody"
    assert meta.image_count == 0


def test_list_engines_surface() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    docreader_pb2_grpc.add_DocReaderServicer_to_server(
        DocReaderServicer(parser=_plain_servicer_parser()), server,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = docreader_pb2_grpc.DocReaderStub(channel)
            resp = stub.ListEngines(docreader_pb2.ListEnginesRequest(), timeout=10)
    finally:
        server.stop(0)
    assert any(e.name == "builtin" for e in resp.engines)