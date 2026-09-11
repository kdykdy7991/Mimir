"""B4.1 — MCP /health probe unit tests (offline/timeout/bad/upstream/SSRF)."""

from __future__ import annotations

import httpx
import pytest

from src.web_api.mcp_connection import (
    MisconfiguredError,
    probe_mcp_health,
    probe_upstream,
)


def _mock(handler):
    """Return an httpx.MockTransport for a URL-path-keyed handler."""
    return httpx.MockTransport(handler)


def _handler_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"status": "ok", "transport": "streamable-http"},
    )


def _handler_bad_body(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"unexpected": True})


def _handler_non_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"<html>not json</html>")


def _handler_server_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, json={"status": "boom"})


async def test_online():
    result = await probe_mcp_health(
        "http://127.0.0.1:8765", timeout_s=5.0, transport=_mock(_handler_ok),
    )
    assert result["status"] == "online"
    # anonymous /health only proves the port answers, never the RAG upstream
    assert result["upstream_status"] == "unknown"
    assert result["mcp_url"] == "http://127.0.0.1:8765/mcp"
    assert result["transport"] == "streamable-http"
    assert result["latency_ms"] is not None


async def test_offline_connection_refused():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    result = await probe_mcp_health(
        "http://127.0.0.1:9999", timeout_s=5.0, transport=_mock(handler),
    )
    assert result["status"] == "offline"
    assert result["upstream_status"] == "offline"
    assert result["latency_ms"] is None


async def test_timeout():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    result = await probe_mcp_health(
        "http://127.0.0.1:8765", timeout_s=5.0, transport=_mock(handler),
    )
    assert result["status"] == "offline"
    assert result["upstream_status"] == "unknown"
    assert result["latency_ms"] is None


async def test_upstream_transport_exception():
    def handler(request):
        raise httpx.RemoteProtocolError("malformed", request=request)

    result = await probe_mcp_health(
        "http://127.0.0.1:8765", timeout_s=5.0, transport=_mock(handler),
    )
    assert result["status"] == "offline"


async def test_bad_response_body_degrades():
    result = await probe_mcp_health(
        "http://127.0.0.1:8765", timeout_s=5.0, transport=_mock(_handler_bad_body),
    )
    assert result["status"] == "degraded"

    non_json = await probe_mcp_health(
        "http://127.0.0.1:8765", timeout_s=5.0, transport=_mock(_handler_non_json),
    )
    assert non_json["status"] == "degraded"


async def test_server_error_degrades():
    result = await probe_mcp_health(
        "http://127.0.0.1:8765", timeout_s=5.0,
        transport=_mock(_handler_server_error),
    )
    assert result["status"] == "degraded"


async def test_empty_base_url_is_misconfigured():
    with pytest.raises(MisconfiguredError):
        await probe_mcp_health("")


async def test_upstream_in_process_always_online():
    assert await probe_upstream(
        backend="in_process", upstream_base_url="", internal_key="",
    ) == "online"


async def test_upstream_http_online():
    def handler(request):
        assert request.headers.get("X-API-Key") == "secret"
        assert not request.url.query
        return httpx.Response(200, json={})

    status = await probe_upstream(
        backend="http", upstream_base_url="https://api.internal:8766",
        internal_key="secret", timeout_s=5.0, transport=_mock(handler),
    )
    assert status == "online"


async def test_upstream_http_internal_auth_failed_is_degraded():
    def handler(request):
        return httpx.Response(401, json={})

    status = await probe_upstream(
        backend="http", upstream_base_url="https://api.internal:8766",
        internal_key="wrong", timeout_s=5.0, transport=_mock(handler),
    )
    assert status == "degraded"


async def test_upstream_http_unreachable_is_offline():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    status = await probe_upstream(
        backend="http", upstream_base_url="https://api.internal:8766",
        internal_key="secret", timeout_s=5.0, transport=_mock(handler),
    )
    assert status == "offline"


async def test_upstream_http_no_entry_returns_unknown():
    assert await probe_upstream(
        backend="http", upstream_base_url="", internal_key="",
    ) == "unknown"


async def test_url_never_comes_from_request():
    """SSRF guard: the probe only ever POSTs to the server-configured base.

    The request body/args cannot influence the URL — there is no URL input at
    all. We assert the probe hits exactly the configured /health endpoint.
    """
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"status": "ok", "transport": "streamable-http"})

    await probe_mcp_health(
        "https://configured.example:9000", timeout_s=5.0, transport=_mock(handler),
    )
    assert seen == ["https://configured.example:9000/health"]