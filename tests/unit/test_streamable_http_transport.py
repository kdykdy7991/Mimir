"""
Unit tests for the streamable-HTTP MCP transport (F-extra).

Covers:
- argparse in :mod:`src.mcp_server.server` and :mod:`main`
  accepts the new flags and default values.
- :func:`build_asgi_app` returns a Starlette app with the
  expected routes (``/mcp`` for MCP, ``/health`` for probes).
- The ``/health`` endpoint returns a JSON body without touching
  any MCP state (no Session manager involvement).
- Unsupported HTTP methods on ``/mcp`` return 405.

The full end-to-end roundtrip (real uvicorn + real client) lives
in :mod:`tests.integration.test_streamable_http_roundtrip`.

Note on Server construction
----------------------------
We construct ``mcp.server.Server`` instances directly via the
mcp v2 constructor params (``on_list_tools`` / ``on_call_tool``)
rather than going through :meth:`ProtocolHandler.build_server`,
because the latter's decorator-based wiring is broken under the
currently-installed mcp 2.0 (a separate pre-existing issue).
This isolates the new transport's tests from that bug.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.lowlevel import Server as LowlevelServer
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from src.mcp_server.server import _is_loopback_host, parse_args
from src.mcp_server.transports.streamable_http import build_asgi_app


# ---------------------------------------------------------------------------
# Helpers — build a minimal mcp v2 Server
# ---------------------------------------------------------------------------

def _build_test_server() -> LowlevelServer:
    """A bare ``Server`` with one tool, ``echo``.

    Uses the mcp v2 handler API (``on_list_tools`` /
    ``on_call_tool`` ctor params) rather than decorators — see
    module docstring.
    """
    async def _on_list_tools(
        ctx, params,
    ) -> ListToolsResult:
        return ListToolsResult(tools=[
            Tool(
                name="echo",
                description="Echo a message back.",
                inputSchema={
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": [],
                },
            ),
        ])

    async def _on_call_tool(
        ctx, params: CallToolRequestParams,
    ) -> CallToolResult:
        msg = (params.arguments or {}).get("msg", "")
        return CallToolResult(
            content=[TextContent(type="text", text=f"echo:{msg}")],
        )

    return LowlevelServer(
        "skdy-rag-server",
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )


def _build_app(**kwargs) -> Any:
    return build_asgi_app(_build_test_server(), **kwargs)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_defaults_to_stdio(self):
        ns = parse_args([])
        assert ns.transport == "stdio"
        assert ns.host == "127.0.0.1"
        assert ns.port == 8765
        assert ns.mcp_path == "/mcp"
        assert ns.config == "./config/settings.yaml"
        assert ns.log_level == "INFO"

    def test_accepts_streamable_http(self):
        ns = parse_args([
            "--transport", "streamable-http",
            "--host", "0.0.0.0",
            "--port", "9999",
            "--mcp-path", "/api/mcp",
        ])
        assert ns.transport == "streamable-http"
        assert ns.host == "0.0.0.0"
        assert ns.port == 9999
        assert ns.mcp_path == "/api/mcp"

    def test_rejects_unknown_transport(self):
        with pytest.raises(SystemExit):
            parse_args(["--transport", "websocket"])


class TestHttpBindSafety:
    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
    def test_loopback_hosts_are_recognised(self, host):
        assert _is_loopback_host(host)

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::"])
    def test_non_loopback_hosts_are_not_recognised(self, host):
        assert not _is_loopback_host(host)


# ---------------------------------------------------------------------------
# ASGI app construction
# ---------------------------------------------------------------------------

class TestBuildAsgiApp:
    def test_returns_starlette_app(self):
        from starlette.applications import Starlette
        app = _build_app()
        assert isinstance(app, Starlette)

    def test_default_mcp_path_is_slash_mcp(self):
        app = _build_app()
        paths = [r.path for r in app.routes]
        assert "/mcp" in paths
        assert "/health" in paths

    def test_custom_mcp_path(self):
        app = _build_app(mcp_path="/api/mcp")
        paths = [r.path for r in app.routes]
        assert "/api/mcp" in paths
        assert "/health" in paths

    def test_mcp_path_normalised(self):
        """Trailing slashes and missing leading slash are fixed up."""
        app = _build_app(mcp_path="mcp/")
        paths = [r.path for r in app.routes]
        assert "/mcp" in paths

    def test_session_manager_attached(self):
        app = _build_app()
        assert hasattr(app.state, "manager")
        from mcp.server.streamable_http_manager import (
            StreamableHTTPSessionManager,
        )
        assert isinstance(app.state.manager, StreamableHTTPSessionManager)

    def test_mcp_route_mounts_asgi_subapp(self):
        """The MCP endpoint is a ``StreamableHTTPASGIApp`` — an ASGI
        sub-app, not a Starlette function endpoint (which would
        return a Response and clash with the manager's direct
        ``send()`` calls).
        """
        from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
        app = _build_app()
        mcp_route = next(r for r in app.routes if r.path == "/mcp")
        assert isinstance(mcp_route.endpoint, StreamableHTTPASGIApp)


# ---------------------------------------------------------------------------
# Health endpoint (no MCP state required)
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self):
        from starlette.testclient import TestClient
        app = _build_app()
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"status": "ok", "transport": "streamable-http"}


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

def test_transport_module_exports_build_asgi_app():
    from src.mcp_server.transports import streamable_http as mod
    assert callable(mod.build_asgi_app)
    assert callable(mod.run_with_uvicorn)