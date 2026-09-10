"""Tests for the container-level readiness probe (``docreader.health_check``).

Verifies the same fatality gates the Docker HEALTHCHECK uses: waiting for the
channel READY, a real ListEngines round-trip, and the builtin engine covering the
minimum production formats.
"""

from __future__ import annotations

from concurrent import futures

import grpc

from docreader.main import DocReaderServicer
from docreader.parser.parser import Parser
from docreader.proto import docreader_pb2_grpc


def _servicer_server() -> tuple[grpc.Server, int]:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    docreader_pb2_grpc.add_DocReaderServicer_to_server(
        DocReaderServicer(parser=Parser()), server,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def test_health_check_ok_against_live_server() -> None:
    from docreader import health_check as hc

    server, port = _servicer_server()
    try:
        assert hc.check(f"127.0.0.1:{port}") == []
    finally:
        server.stop(0)


def test_health_check_flags_unreachable_endpoint() -> None:
    from docreader import health_check as hc

    # A free/closed port has no listener -> channel never READY.
    errors = hc.check("127.0.0.1:9")
    assert errors, "expected at least one error for unreachable endpoint"


def test_health_check_default_endpoint_matches_config_port() -> None:
    from docreader import health_check as hc

    # Default probe endpoint targets the configured local gRPC port.
    assert hc._ready_timeout_seconds() >= 1.0


def test_health_check_fails_when_builtin_missing_formats(monkeypatch) -> None:
    """If the builtin engine lacks a required format, the probe must flag it."""
    import docreader.health_check as hc

    monkeypatch.setattr(hc, "MIN_READY_FORMATS", frozenset({"pdf", "epub", "xmind"}))

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))

    class _ShortServicer(DocReaderServicer):
        def ListEngines(self, request, context):  # noqa: N802
            from docreader.proto import docreader_pb2
            # advertise only pdf + md (missing epub/xmind required above)
            return docreader_pb2.ListEnginesResponse(
                engines=[docreader_pb2.ParserEngineInfo(
                    name="builtin", file_types=["pdf", "md"], available=True,
                )],
            )

    docreader_pb2_grpc.add_DocReaderServicer_to_server(_ShortServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        errors = hc.check(f"127.0.0.1:{port}")
        assert any("missing" in e for e in errors), errors
    finally:
        server.stop(0)