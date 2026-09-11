"""B4.3 — full-path MCP connection test (real uvicorn + Streamable HTTP).

Spins up a real MCP ``streamable-http`` server (uvicorn in a thread,
guarded by a real API-key service on a tmp DB), then drives
``POST /api/v1/mcp-server/test-connection`` through a FastAPI TestClient
against it over genuine HTTP. Covers: correct key, wrong key, revoked key,
empty scope, tools missing, and asserts the key never leaks into the
response or the process logs.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Iterator

import pytest
import uvicorn

from src.mcp_server.auth import ApiKeyService
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.transports.streamable_http import build_asgi_app
from src.web_api.app import create_app
from src.web_api.routers import mcp_server as mcp_server_router
from src.web_api.routers.mcp_server import McpServerRouteConfig

_LOG = logging.getLogger("test.mcp.test-connection")

LIST_COLLECTIONS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "count": {"type": "integer"},
        "collections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    "required": ["count", "collections"],
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_mcp_app(handler: ProtocolHandler, key_service):
    return build_asgi_app(handler.build_server(), key_service=key_service)


def _build_handler(*, collection_count: int = 2, with_list_collections: bool = True) -> ProtocolHandler:
    handler = ProtocolHandler(server_name="skdy-rag-server")

    async def _echo(args) -> str:
        return f"echo:{args.get('msg', '')}"

    handler.register(
        name="echo", description="Echo a message.",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
        handler=_echo,
    )

    if with_list_collections:
        async def _list_collections(args) -> tuple[str, dict]:
            md_lines = [f"# Collections ({collection_count})", ""]
            structured = {
                "count": collection_count,
                "n_collections": collection_count,
                "collections": [
                    {"name": f"kb-{i}", "description": None,
                     "document_count": None, "chunk_count": None}
                    for i in range(collection_count)
                ],
            }
            return "\n".join(md_lines), structured

        handler.register(
            name="list_collections",
            description="List authorized collections.",
            input_schema={"type": "object", "properties": {}},
            handler=_list_collections,
            output_schema=LIST_COLLECTIONS_OUTPUT_SCHEMA,
        )
    return handler


class _McpUvicorn:
    def __init__(self, handler: ProtocolHandler, key_service):
        self._app = _build_mcp_app(handler, key_service)
        self._port = _free_port()
        config = uvicorn.Config(
            self._app, host="127.0.0.1", port=self._port,
            log_level="warning", access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, daemon=True, name="uvicorn-mcp-b4",
        )

    def start(self) -> str:
        self._thread.start()
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if self._server.started:
                return f"http://127.0.0.1:{self._port}"
            time.sleep(0.02)
        raise RuntimeError("MCP uvicorn did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture
def mcp_env(tmp_path):
    """Spin up a real MCP server + FastAPI app wired to it.

    Yields ``(client, base_url, service)`` with ``service`` the shared
    :class:`ApiKeyService` (used by both the MCP server and the record).
    """
    service = ApiKeyService(db_path=tmp_path / "mcp_access.db")
    handler = _build_handler()
    instance = _McpUvicorn(handler, service)
    base_url = instance.start()

    # FastAPI app with the B4 router mounted (the coordinator wires it into
    # production ``create_app``; for these tests we mount it ourselves).
    app = create_app()
    app.include_router(mcp_server_router.router, prefix="/api/v1")

    def fake_config():
        return McpServerRouteConfig(
            base_url=base_url,
            key_db_path=str(tmp_path / "mcp_access.db"),
            timeout_s=10.0,
        )

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mcp_server_router, "_server_config", fake_config)
    monkey.setattr(mcp_server_router, "_rate_limiter",
                   mcp_server_router.RuntimeRateLimiter(
                       max_requests=1000, window_seconds=60.0))

    from fastapi.testclient import TestClient
    client = TestClient(app)

    try:
        yield client, base_url, service
    finally:
        client.close()
        monkey.undo()
        instance.stop()


def _make_key(service, name="demo"):
    raw_key, meta = service.create_key(name=name, allowed_collections={"kb-1"})
    return raw_key


def test_correct_key_full_path(mcp_env):
    client, base_url, service = mcp_env
    raw_key = _make_key(service)
    resp = client.post("/api/v1/mcp-server/test-connection", json={"api_key": raw_key})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert str(body["tested_at"]).endswith("Z")
    names = [s["name"] for s in body["stages"]]
    assert names == ["connect", "initialize", "tools_list", "list_collections"]
    assert all(s["status"] == "success" for s in body["stages"])
    tools = next(s for s in body["stages"] if s["name"] == "tools_list")
    assert tools["tool_count"] == 2  # echo + list_collections
    cols = next(s for s in body["stages"] if s["name"] == "list_collections")
    assert cols["collection_count"] == 2
    # the key must never appear anywhere in the response
    assert raw_key not in resp.text


def test_wrong_key_is_client_unauthorized(mcp_env, caplog):
    client, _, _ = mcp_env
    with caplog.at_level(logging.DEBUG, logger="test"):
        resp = client.post(
            "/api/v1/mcp-server/test-connection",
            json={"api_key": "skdy_mcp_wrongkey.iamnotreal"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "client_unauthorized"
    assert body["stages"][0]["status"] == "failed"
    # stages after connect are skipped
    assert [s["status"] for s in body["stages"][1:]] == ["skipped"] * 3


def test_revoked_key_is_client_key_revoked(mcp_env):
    client, _, service = mcp_env
    raw_key = _make_key(service, name="revokeme")
    service.revoke_key(name="revokeme")
    resp = client.post("/api/v1/mcp-server/test-connection", json={"api_key": raw_key})
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "client_key_revoked"


def test_empty_scope(mcp_env, tmp_path):
    service = mcp_env[2]
    raw_key = _make_key(service, name="noscope")

    # Rebuild MCP server to return 0 collections (authorized, no data).
    handler = _build_handler(collection_count=0)
    instance = _McpUvicorn(handler, service)
    base_url = instance.start()

    app = create_app()
    app.include_router(mcp_server_router.router, prefix="/api/v1")

    from fastapi.testclient import TestClient
    monkey = pytest.MonkeyPatch()
    monkey.setattr(mcp_server_router, "_server_config", lambda: McpServerRouteConfig(
        base_url=base_url, key_db_path=str(tmp_path / "mcp_access.db"), timeout_s=10.0))
    monkey.setattr(mcp_server_router, "_rate_limiter",
                   mcp_server_router.RuntimeRateLimiter(max_requests=1000, window_seconds=60.0))
    client = TestClient(app)
    try:
        resp = client.post("/api/v1/mcp-server/test-connection", json={"api_key": raw_key})
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "empty_scope"
    finally:
        client.close()
        monkey.undo()
        instance.stop()


def test_tools_missing_is_unexpected(mcp_env, tmp_path):
    service = mcp_env[2]
    raw_key = _make_key(service, name="toolsmissing")
    handler = _build_handler(with_list_collections=False)  # no list_collections tool
    instance = _McpUvicorn(handler, service)
    base_url = instance.start()

    app = create_app()
    app.include_router(mcp_server_router.router, prefix="/api/v1")
    from fastapi.testclient import TestClient
    monkey = pytest.MonkeyPatch()
    monkey.setattr(mcp_server_router, "_server_config", lambda: McpServerRouteConfig(
        base_url=base_url, key_db_path=str(tmp_path / "mcp_access.db"), timeout_s=10.0))
    monkey.setattr(mcp_server_router, "_rate_limiter",
                   mcp_server_router.RuntimeRateLimiter(max_requests=1000, window_seconds=60.0))
    client = TestClient(app)
    try:
        resp = client.post("/api/v1/mcp-server/test-connection", json={"api_key": raw_key})
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "unexpected_response"
    finally:
        client.close()
        monkey.undo()
        instance.stop()


def test_key_never_in_logs_or_response(mcp_env, caplog):
    """Security: the raw key must not leak into process logs or the body."""
    client, _, service = mcp_env
    raw_key = _make_key(service, name="leakcheck")
    with caplog.at_level(logging.DEBUG):
        resp = client.post("/api/v1/mcp-server/test-connection", json={"api_key": raw_key})
    assert resp.status_code == 200
    assert raw_key not in resp.text
    assert raw_key not in caplog.text
    # even the secret part alone must not appear
    secret = raw_key.rsplit(".", 1)[1]
    assert secret not in caplog.text
    assert secret not in resp.text


async def test_mcp_status_endpoint_reports_online(tmp_path):
    """B4.1 end-to-end: online health against a real MCP server."""
    # /health is anonymous; the backend can use a throwaway store.
    service = ApiKeyService(db_path=tmp_path / "mcp_access.db")
    handler = _build_handler()
    instance = _McpUvicorn(handler, service)
    base_url = instance.start()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(mcp_server_router, "_server_config", lambda: McpServerRouteConfig(
        base_url=base_url, key_db_path=str(tmp_path / "mcp_access.db"), timeout_s=10.0))
    from fastapi.testclient import TestClient
    try:
        with TestClient(create_app_with_router()) as client:
            resp = client.get("/api/v1/mcp-server/status")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "online"
            assert body["mcp_url"] == f"{base_url}/mcp"
            assert body["upstream_status"] == "online"
            assert body["latency_ms"] is not None
    finally:
        monkey.undo()
        instance.stop()


def create_app_with_router():
    app = create_app()
    app.include_router(mcp_server_router.router, prefix="/api/v1")
    return app