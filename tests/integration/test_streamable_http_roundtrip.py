"""
Integration tests for the streamable-HTTP MCP transport (F-extra).

Spins up the ASGI app on a real port via uvicorn (in a background
thread), then connects with the official ``streamable_http_client``
and exercises the full MCP lifecycle:

- ``initialize`` handshake returns server info + capabilities
- ``tools/list`` returns the registered tools
- ``tools/call`` invokes a registered tool and returns its result
- Multiple concurrent clients each get their own session
- GET on ``/health`` returns the liveness JSON

Uses a free port allocated by the OS so the tests are
parallel-safe. Each test gets its own uvicorn instance so
shutdown is deterministic.

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

import asyncio
import socket
import threading
import time
from typing import Iterator

import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server as LowlevelServer
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from src.mcp_server.transports.streamable_http import build_asgi_app


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Ask the OS for an unused TCP port. Race-prone in theory but
    fine in practice — the port is released as soon as we close
    the socket, and the test binds it again within microseconds.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_test_app() -> object:
    """An ASGI app with two tools: ``echo`` and ``add``."""
    async def _on_list_tools(ctx, params) -> ListToolsResult:
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
            Tool(
                name="add",
                description="Add two integers.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                    "required": ["a", "b"],
                },
            ),
        ])

    async def _on_call_tool(
        ctx, params: CallToolRequestParams,
    ) -> CallToolResult:
        args = params.arguments or {}
        if params.name == "echo":
            return CallToolResult(
                content=[TextContent(
                    type="text", text=f"echo:{args.get('msg', '')}",
                )],
            )
        if params.name == "add":
            return CallToolResult(
                content=[TextContent(
                    type="text", text=str(int(args.get("a", 0))
                                          + int(args.get("b", 0))),
                )],
            )
        return CallToolResult(
            content=[TextContent(type="text", text="Unknown tool")],
            is_error=True,
        )

    server = LowlevelServer(
        "skdy-rag-server",
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )
    return build_asgi_app(server)


class _UvicornInThread:
    """Run a uvicorn.Server in a daemon thread; provide ``url``."""

    def __init__(self, app, *, port: int):
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port,
            log_level="warning", access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, daemon=True, name="uvicorn-test",
        )
        self.port = port

    def start(self) -> None:
        self._thread.start()
        # Wait for the server to start serving (max 5s).
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self._server.started:
                return
            time.sleep(0.02)
        raise RuntimeError("uvicorn did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture
def server_url() -> Iterator[str]:
    """Yield a base URL (``http://127.0.0.1:<port>``) with a live
    uvicorn instance backing it. The server is torn down at the end
    of the test.
    """
    port = _free_port()
    instance = _UvicornInThread(_build_test_app(), port=port)
    instance.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        instance.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initialize_returns_server_info(server_url):
    async with streamable_http_client(f"{server_url}/mcp") as (r, w):
        async with ClientSession(r, w) as session:
            init = await session.initialize()
            assert init.server_info is not None
            assert init.server_info.name == "skdy-rag-server"
            assert init.capabilities is not None


@pytest.mark.asyncio
async def test_tools_list(server_url):
    async with streamable_http_client(f"{server_url}/mcp") as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            assert names == ["add", "echo"]


@pytest.mark.asyncio
async def test_call_echo_tool(server_url):
    async with streamable_http_client(f"{server_url}/mcp") as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(
                "echo", arguments={"msg": "hello"},
            )
            assert not result.is_error
            assert result.content[0].text == "echo:hello"


@pytest.mark.asyncio
async def test_call_add_tool(server_url):
    async with streamable_http_client(f"{server_url}/mcp") as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(
                "add", arguments={"a": 2, "b": 40},
            )
            assert not result.is_error
            assert result.content[0].text == "42"


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(server_url):
    async with streamable_http_client(f"{server_url}/mcp") as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool("no_such_tool", arguments={})
            assert result.is_error


@pytest.mark.asyncio
async def test_concurrent_sessions_have_independent_state(server_url):
    """Two clients in parallel: each gets its own session_id and
    can call tools without interfering with the other.
    """
    async def run(label: str) -> str:
        async with streamable_http_client(f"{server_url}/mcp") as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.call_tool(
                    "echo", arguments={"msg": label},
                )
                return result.content[0].text

    a, b = await asyncio.gather(run("alpha"), run("beta"))
    assert a == "echo:alpha"
    assert b == "echo:beta"


@pytest.mark.asyncio
async def test_health_endpoint(server_url):
    """The /health probe should return 200 OK without speaking MCP."""
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{server_url}/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "transport": "streamable-http"}