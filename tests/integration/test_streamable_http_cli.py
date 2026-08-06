"""
Integration test: launch the actual CLI as a subprocess in HTTP mode
and verify a real MCP client can talk to it end-to-end.

This exercises the full production code path — ``main.py`` →
``run_server()`` → ``ProtocolHandler.build_server()`` →
``build_asgi_app()`` → uvicorn → ``streamable_http_client``
— and asserts the three registered tools are reachable over HTTP.

The CLI is launched exactly the way a remote agent would launch it,
just pointed at a random localhost port instead of 0.0.0.0. We
deliberately do NOT mock the tool handlers so the test catches
transport / wiring bugs even when the underlying backends work.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_health(
    proc: subprocess.Popen, url: str, *, timeout: float = 20.0,
) -> bool:
    """Poll /health until 200 OK or ``timeout`` elapses.

    Also fails fast if the subprocess exits early (boot crash, port
    already bound) so the fixture can report the real stderr instead of
    waiting out the timeout.
    """
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False  # process died before health came up
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


@pytest.fixture
def http_server():
    """Launch ``python main.py --transport streamable-http`` as a
    subprocess and yield its base URL. Teardown kills the process.
    """
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "main",
            "--transport", "streamable-http",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "WARNING",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        if not _wait_for_health(proc, f"{base}/health"):
            proc.kill()
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            raise RuntimeError(
                f"server failed to come up on {base}/health "
                f"(exit={proc.poll()}):\n{stderr}",
            )
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.mark.asyncio
async def test_real_cli_serves_real_protocol_handler_over_http(http_server):
    """The big end-to-end: subprocess CLI → real ProtocolHandler →
    real streamable-http → real mcp client.

    Verifies:
    - The CLI actually boots under the new transport (no
      'Server has no attribute list_tools' regressions).
    - The three tools registered by
      ``_register_default_tools`` (query_knowledge_hub,
      list_collections, get_document_summary) are exposed.
    - ``tools/list`` and at least one ``tools/call`` roundtrip
      successfully — proving the v2 on_call_tool wiring works
      through the full stack.
    """
    async with streamable_http_client(f"{http_server}/mcp") as (r, w):
        async with ClientSession(r, w) as session:
            init = await session.initialize()
            assert init.server_info.name == "skdy-rag-server"

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            assert names == [
                "get_document_summary",
                "list_collections",
                "query_knowledge_hub",
            ]

            # ---- all three REAL RAG tools over HTTP -------------

            # 1) list_collections — scans the data dir, no external dep.
            result = await session.call_tool(
                "list_collections", arguments={},
            )
            assert result is not None
            assert not result.is_error
            assert any(
                c.type == "text" and "Collections" in c.text
                for c in result.content
            )

            # 2) get_document_summary — known business error → is_error
            #    (a tool-level result, not a protocol MCPError).
            missing = await session.call_tool(
                "get_document_summary",
                arguments={"doc_id": "definitely-not-a-real-id"},
            )
            assert missing.is_error
            assert "document not found" in missing.content[0].text

            # 3) query_knowledge_hub — runs the REAL retrieval pipeline
            #    (dense + sparse + fusion) over HTTP. With no data it
            #    returns an empty/degraded result, never an error.
            q = await session.call_tool(
                "query_knowledge_hub",
                arguments={"query": "vector search", "top_k": 5},
            )
            assert q.is_error is False
            assert any(c.type == "text" for c in q.content)