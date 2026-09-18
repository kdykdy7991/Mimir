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
            r = httpx.get(url, timeout=1.0, trust_env=False)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def _embedding_base_url() -> str:
    """Configured embedding endpoint (may be empty for local models)."""
    from src.core.settings import load_settings

    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    return str(getattr(settings.embedding, "base_url", "") or "")


def _embedding_endpoint_reachable() -> bool:
    """True when the configured embedding backend can actually initialize.

    A listening TCP socket is insufficient: an incompatible OpenAI-style
    ``/models`` response still makes real retrieval unusable.  Exercise the
    same factory as production so this optional live-provider test only runs
    when the provider is genuinely ready.
    """
    try:
        from src.libs.embedding import EmbeddingFactory

        settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
        EmbeddingFactory.create(settings.embedding)
        return True
    except Exception:
        return False


def _read_bm25_collections() -> list[str]:
    """Discover the collection names the real ``list_collections`` tool
    will report (BM25 index files ∪ the configured ``default``), so the
    test key can be granted exactly what the CLI tool needs to succeed.

    Mirrors ``InProcessReadonlyClient._list_bm25`` + the configured
    collection merge in ``list_collections``; granting a superset is
    harmless because the tool intersects the key's grant with the
    collections that actually exist.
    """
    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")

    bm25_dir = REPO_ROOT / "data" / "db" / "bm25"
    bm25 = {p.stem for p in bm25_dir.glob("*.json")} if bm25_dir.is_dir() else set()
    configured = settings.vector_store.collection_name
    names = bm25 | ({configured} if configured else set())
    return sorted(names)


@pytest.fixture
def http_server(tmp_path):
    """Launch ``python main.py --transport streamable-http`` as a
    subprocess and yield its base URL. Teardown kills the process.

    When ``mcp_access.enabled`` is true (the shipped default), the CLI
    requires a Bearer API key on ``/mcp``. We create a key granting every
    discovered collection so the test can talk to the real tools, and
    pass it to the MCP client via the standard header.
    """
    config_path = tmp_path / "settings.yaml"
    config_text = (REPO_ROOT / "config" / "settings.yaml").read_text(
        encoding="utf-8",
    )
    config_text = config_text.replace(
        "database_path: ./data/mcp/db/mcp_access.db",
        f"database_path: {tmp_path / 'mcp_access.db'}",
    )
    config_path.write_text(config_text, encoding="utf-8")
    settings = load_settings(config_path)
    raw = None
    service = None
    key_name = f"cli-it-{os.getpid()}-{id(tmp_path)}"
    if settings.mcp_access.enabled:
        from src.mcp_server.auth import ApiKeyService

        service = ApiKeyService(db_path=settings.mcp_access.database_path)
        raw, _meta = service.create_key(
            name=key_name,
            allowed_collections=set(_read_bm25_collections()),
        )

    port = _free_port()
    subprocess_env = os.environ.copy()
    for name in (
        "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
        "HTTPS_PROXY", "https_proxy",
    ):
        subprocess_env.pop(name, None)
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
        env=subprocess_env,
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
                service.revoke_key(name=key_name)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_real_cli_serves_real_protocol_handler_over_http(http_server):
    """The big end-to-end: subprocess CLI → real ProtocolHandler →
    real streamable-http → real mcp client.

    Verifies:
    - The CLI actually boots under the new transport (no
      'Server has no attribute list_tools' regressions).
    - The nine read-only tools registered by ``_register_default_tools``
      are exposed, including ``search_chunks``.
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
            trust_env=False,
        )
    async with streamable_http_client(
        f"{http_server[0]}/mcp", http_client=http_client,
    ) as (r, w):
        async with ClientSession(r, w) as session:
            init = await session.initialize()
            assert init.server_info.name == "skdy-knowledge-query"

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            assert names == [
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

            # ---- REAL RAG tools over HTTP (no external dependency) ----

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

            # 3) query_knowledge_hub over HTTP needs a live embedding
            #    provider, so it lives in its own test below (see
            #    test_query_knowledge_hub_over_http_with_live_embedding).


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _embedding_endpoint_reachable(),
    reason=(
        f"live embedding provider unreachable ({_embedding_base_url() or 'n/a'}); "
        "retrieval over HTTP is covered by the unit suite and the "
        "Task 01 eval Gate"
    ),
)
async def test_query_knowledge_hub_over_http_with_live_embedding(http_server):
    """Runs the REAL retrieval pipeline (dense + sparse + fusion) over
    HTTP. With no data it returns an empty/degraded result, never an
    error. The key has multiple grants, so §6.1 requires an explicit
    ``collection``.

    Skipped only when the configured embedding endpoint is not listening
    (the usual state on a dev box, and the reason Task 01's CI baseline
    uses a deterministic hash embedder) — never to hide a transport bug:
    with the endpoint up the call must succeed.
    """
    http_client = None
    if http_server[1]:
        import httpx
        http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {http_server[1]}"},
            trust_env=False,
        )
    async with streamable_http_client(
        f"{http_server[0]}/mcp", http_client=http_client,
    ) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            q = await session.call_tool(
                "query_knowledge_hub",
                arguments={"query": "vector search", "top_k": 5, "collection": "default"},
            )
            assert q.is_error is False
            assert any(c.type == "text" for c in q.content)
