"""
Integration test (Task 02.4/02.5): the capability Resource is served by
both transports from one shared ``Server`` registration.

Why this test exists
--------------------
``rag://server/capabilities`` is registered inside
``ProtocolHandler.build_server(capabilities=...)``, and the CLI hands that
single ``Server`` object to either the stdio runner or the
streamable-http runner. This test proves the consequence end-to-end with
the *real* CLI as a subprocess:

- ``python -m main --transport stdio``        → resources/list + resources/read
- ``python -m main --transport streamable-http`` → same, byte-identical body

Both must also expose the same twelve tools, while the Resource remains
additive rather than appearing as a tool.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_URI = "rag://server/capabilities"
EXPECTED_TOOLS = [
    "get_chunk",
    "get_chunk_context",
    "get_document",
    "get_document_chunks",
    "get_document_summary",
    "get_sync_status",
    "list_collections",
    "list_data_sources",
    "list_documents",
    "list_sync_failures",
    "query_knowledge_hub",
    "search_chunks",
]


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "main", "--config", "./config/settings.yaml",
              "--log-level", "WARNING"],
        cwd=str(REPO_ROOT),
    )


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_for_health(proc: subprocess.Popen, url: str, *, timeout: float = 60.0) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(url, timeout=1.0, trust_env=False).status_code == 200:
                return True
        except Exception:  # noqa: BLE001 - retry until the deadline
            pass
        time.sleep(0.1)
    return False


@pytest.fixture
def http_server(tmp_path):
    """Boot the CLI over streamable-http, with a Bearer key when auth is
    enabled (the shipped default) so the client can reach ``/mcp``."""
    from src.core.settings import load_settings

    config_path = tmp_path / "settings.yaml"
    config_text = (REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    config_text = config_text.replace(
        "database_path: ./data/mcp/db/mcp_access.db",
        f"database_path: {tmp_path / 'mcp_access.db'}",
    )
    config_path.write_text(config_text, encoding="utf-8")
    settings = load_settings(config_path)
    raw = None
    service = None
    if settings.mcp_access.enabled:
        from src.mcp_server.auth import ApiKeyService

        service = ApiKeyService(db_path=settings.mcp_access.database_path)
        raw, _meta = service.create_key(
            name=f"caps-it-{id(http_server)}",
            allowed_collections={"default"},
        )

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "main",
            "--transport", "streamable-http",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--config", str(config_path),
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
            stderr = (
                proc.stderr.read().decode("utf-8", errors="replace")
                if proc.stderr else ""
            )
            raise RuntimeError(
                f"server failed to come up on {base}/health "
                f"(exit={proc.poll()}):\n{stderr}",
            )
        yield base, raw
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if service is not None and raw is not None:
            try:
                service.revoke_key(name=f"caps-it-{id(http_server)}")
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass


async def _read_over_stdio() -> tuple[str, list[str], dict]:
    """Return (capabilities body, tool names, list_resources URI map)."""
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resources = await session.list_resources()
            body = (await session.read_resource(CAPABILITIES_URI)).contents[0].text
            tools = sorted(t.name for t in (await session.list_tools()).tools)
            return body, tools, {
                str(r.uri): r.mime_type for r in resources.resources
            }


async def _read_over_http(base: str, key: str | None) -> tuple[str, list[str], dict]:
    http_client = None
    if key:
        import httpx

        http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {key}"},
            trust_env=False,
        )
    try:
        async with streamable_http_client(
            f"{base}/mcp", http_client=http_client,
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resources = await session.list_resources()
                body = (await session.read_resource(CAPABILITIES_URI)).contents[0].text
                tools = sorted(t.name for t in (await session.list_tools()).tools)
                return body, tools, {
                    str(r.uri): r.mime_type for r in resources.resources
                }
    finally:
        if http_client is not None:
            await http_client.aclose()


@pytest.mark.asyncio
async def test_capabilities_are_identical_over_stdio_and_http(http_server):
    stdio_body, stdio_tools, stdio_resources = await _read_over_stdio()
    http_body, http_tools, http_resources = await _read_over_http(*http_server)

    # One registration, two transports: identical bytes and identical
    # tool surface. A drift here means capability facts are assembled
    # per-transport instead of per-server.
    assert http_body == stdio_body
    assert http_tools == stdio_tools == EXPECTED_TOOLS
    assert stdio_resources == http_resources
    assert stdio_resources[CAPABILITIES_URI] == "application/json"


@pytest.mark.asyncio
async def test_capabilities_body_matches_the_live_registry_and_budget():
    import json

    from src.core.settings import load_settings
    from src.mcp_server.capabilities import build_capabilities
    from src.mcp_server.presentation.budgets import budget_from_settings
    from src.mcp_server.protocol_handler import ProtocolHandler
    from src.mcp_server.server import _register_default_tools

    body, _tools, _resources = await _read_over_stdio()
    served = json.loads(body)

    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    budget = budget_from_settings(settings)
    handler = ProtocolHandler()
    _register_default_tools(handler)
    expected = build_capabilities(
        handler,
        budget=budget,
        rerank_backend=settings.rerank.backend,
        server_name=settings.mcp.server_name,
    )
    # The served document is the same fact set the server was built from:
    # real registry, Settings-derived budget, configured rerank backend.
    assert {t["name"] for t in served["tools"]} == set(handler.list_names())
    assert served["limits"] == expected["limits"]
    assert served["limits"]["top_k_max"] == settings.mcp_limits.top_k_max
    assert served["pagination"]["page_size"]["default"] == (
        settings.mcp_limits.page_size_default
    )
    assert served["retrieval"]["rerank"] == expected["retrieval"]["rerank"]
    assert served["server_name"] == settings.mcp.server_name
