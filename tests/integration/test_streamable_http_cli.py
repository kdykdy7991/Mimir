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
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from src.core.settings import load_settings

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


def _read_bm25_collections() -> list[str]:
    """Discover the collection names the real ``list_collections`` tool
    will report (BM25 dirs ∪ the configured ``default``), so the test key
    can be granted exactly what the CLI tool needs to succeed."""
    from src.mcp_server.tools.list_collections import _list_bm25
    from src.core.settings import load_settings

    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    bm25 = set(_list_bm25(str(REPO_ROOT / "data")).keys())
    configured = settings.vector_store.collection_name
    names = bm25 | ({configured} if configured else set())
    return sorted(names)


@pytest.fixture
def http_server():
    """Launch ``python main.py --transport streamable-http`` as a
    subprocess and yield its base URL. Teardown kills the process.

    When ``mcp_access.enabled`` is true (the shipped default), the CLI
    requires a Bearer API key on ``/mcp``. We create a key granting every
    discovered collection so the test can talk to the real tools, and
    pass it to the MCP client via the standard header.
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
        # Issue a key for this test run, in the DB the CLI opens
        # (settings.mcp_access.database_path). If auth is disabled the
        # key is harmless.
        from src.mcp_server.auth import ApiKeyService

        settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
        if settings.mcp_access.enabled:
            service = ApiKeyService(db_path=settings.mcp_access.database_path)
            raw, _meta = service.create_key(
                name=f"cli-it-{os.getpid()}",
                allowed_collections=set(_read_bm25_collections()),
            )
        else:
            raw = None
        yield base, raw
        if raw is not None:
            try:
                service.revoke_key(name=f"cli-it-{os.getpid()}")
            except Exception:
                pass
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
    # The MCP client wraps httpx; pass an httpx client that carries the
    # Bearer header when auth is enabled.
    http_client = None
    if http_server[1]:
        import httpx
        http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {http_server[1]}"},
        )
    async with streamable_http_client(
        f"{http_server[0]}/mcp", http_client=http_client,
    ) as (r, w):
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
            #    The key has multiple grants, so §6.1 requires an explicit
            #    ``collection``.
            q = await session.call_tool(
                "query_knowledge_hub",
                arguments={"query": "vector search", "top_k": 5, "collection": "default"},
            )
            assert q.is_error is False
            assert any(c.type == "text" for c in q.content)