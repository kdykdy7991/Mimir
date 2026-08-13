"""
Unit tests for the MCP Bearer authentication middleware
(:mod:`src.mcp_server.auth.middleware`) and its wiring into
:func:`build_asgi_app`.

Covers PRD §5.1 / §5.3:

- ``/health`` stays anonymous (200).
- ``/mcp`` requires a valid ``Authorization: Bearer`` header.
- Missing / non-Bearer / malformed header  → 401 + ``WWW-Authenticate``.
- Unknown key / wrong secret / revoked key → 401 (identical body).
- Valid key → principal injected into the ASGI scope; request passes.
- A broken key store fails closed at app build time.
- The mcp-library ``scope["user"]`` is populated so cross-key session
  reuse is rejected by the library's own session-owner check.
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
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from src.mcp_server.auth import ApiKeyService
from src.mcp_server.auth.middleware import MCPAccessAuthMiddleware, SCOPE_PRINCIPAL
from src.mcp_server.transports.streamable_http import build_asgi_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_test_server() -> LowlevelServer:
    """Bare mcp v2 Server with one tool that returns the authenticated identity."""

    async def _on_list_tools(ctx, params) -> ListToolsResult:
        return ListToolsResult(tools=[
            Tool(
                name="whoami",
                description="Return the authenticated principal.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
        ])

    async def _on_call_tool(ctx, params: CallToolRequestParams) -> CallToolResult:
        scope = getattr(ctx, "request", None).scope
        principal = scope.get(SCOPE_PRINCIPAL)
        text = (
            f"{principal.name}:{sorted(principal.allowed_collections)}"
            if principal is not None else "anonymous"
        )
        return CallToolResult(content=[TextContent(type="text", text=text)])

    return LowlevelServer(
        "skdy-rag-server",
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )


def _app(key_service: ApiKeyService | None) -> Any:
    return build_asgi_app(_build_test_server(), key_service=key_service)


@pytest.fixture
def service(tmp_path) -> ApiKeyService:
    return ApiKeyService(db_path=tmp_path / "mcp_access.db")


@pytest.fixture
def keys(service):
    """One valid key and one revoked key, plus a second valid key."""
    raw_a, _ = service.create_key(name="agent-a", allowed_collections={"hr", "policy"})
    raw_b, _ = service.create_key(name="agent-b", allowed_collections={"finance"})
    revoked_raw, _ = service.create_key(name="revoked", allowed_collections={"hr"})
    service.revoke_key(name="revoked")
    return {
        "a": raw_a,
        "b": raw_b,
        "revoked": revoked_raw,
        "service": service,
    }


def _post(client: TestClient, url: str = "/mcp", **kwargs):
    return client.post(url, **kwargs)


# ---------------------------------------------------------------------------
# Health stays anonymous
# ---------------------------------------------------------------------------

class TestHealthAnonymous:
    def test_health_without_key_succeeds(self, tmp_path) -> None:
        service = ApiKeyService(db_path=tmp_path / "mcp_access.db")
        app = _app(service)
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 401 semantics
# ---------------------------------------------------------------------------

class TestUnauthorized:
    def test_missing_header_returns_401(self, keys) -> None:
        with TestClient(_app(keys["service"])) as client:
            resp = _post(client)
        assert resp.status_code == 401
        assert resp.json() == {"error": "unauthorized"}
        assert resp.headers["www-authenticate"] == "Bearer"

    def test_non_bearer_scheme_returns_401(self, keys) -> None:
        raw_a = keys["a"]
        with TestClient(_app(keys["service"])) as client:
            resp = _post(
                client,
                headers={"Authorization": f"Basic {raw_a}"},
                content=b'{"jsonrpc":"2.0"}',
            )
        assert resp.status_code == 401

    def test_empty_bearer_returns_401(self, keys) -> None:
        with TestClient(_app(keys["service"])) as client:
            resp = _post(
                client,
                headers={"Authorization": "Bearer "},
                content=b'{"jsonrpc":"2.0"}',
            )
        assert resp.status_code == 401

    def test_unknown_key_returns_401(self, keys) -> None:
        with TestClient(_app(keys["service"])) as client:
            resp = _post(
                client,
                headers={"Authorization": "Bearer skdy_mcp_nokey.some-secret"},
                content=b'{"jsonrpc":"2.0"}',
            )
        assert resp.status_code == 401
        assert resp.json() == {"error": "unauthorized"}

    def test_wrong_secret_returns_401(self, keys) -> None:
        raw_a = keys["a"]
        key_id = raw_a[len("skdy_mcp_"):].split(".", 1)[0]
        wrong = f"skdy_mcp_{key_id}.wrong-secret-value"
        with TestClient(_app(keys["service"])) as client:
            resp = _post(
                client,
                headers={"Authorization": f"Bearer {wrong}"},
                content=b'{"jsonrpc":"2.0"}',
            )
        assert resp.status_code == 401

    def test_revoked_key_returns_401(self, keys) -> None:
        with TestClient(_app(keys["service"])) as client:
            resp = _post(
                client,
                headers={"Authorization": f"Bearer {keys['revoked']}"},
                content=b'{"jsonrpc":"2.0"}',
            )
        assert resp.status_code == 401

    def test_all_failures_share_identical_body(self, keys) -> None:
        """PRD: credential failures are indistinguishable externally."""
        from starlette.testclient import TestClient as TC
        cases = [
            {},
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer skdy_mcp_nokey.s"},
            {"Authorization": f"Bearer {keys['revoked']}"},
        ]
        bodies: set[tuple[int, tuple[tuple[str, str], ...]]] = set()
        with TC(_app(keys["service"])) as client:
            for headers in cases:
                resp = _post(
                    client, headers=headers, content=b'{"jsonrpc":"2.0"}',
                )
                body_items = tuple(sorted(resp.json().items()))
                bodies.add((resp.status_code, body_items))
        assert bodies == {(401, (("error", "unauthorized"),))}


# ---------------------------------------------------------------------------
# Success path — principal injection
# ---------------------------------------------------------------------------

class TestAuthorized:
    def test_valid_key_requests_pass_through(self, keys) -> None:
        with TestClient(_app(keys["service"])) as client:
            resp = _post(
                client,
                headers={"Authorization": f"Bearer {keys['a']}"},
                content=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            )
        # The request reached the MCP app (not a 401); a bare tools/list
        # without a session may still 4xx from the manager, but never 401.
        assert resp.status_code != 401

    def test_principal_injected_into_scope_for_tool(self, keys) -> None:
        """A full stateful MCP exchange (initialize then tools/call) lets
        the tool read ``SCOPE_PRINCIPAL`` and echo the authenticated
        identity back — proving the middleware ran before the JSON-RPC
        dispatch and that the principal reached the tool."""
        with TestClient(_app(keys["service"])) as client:
            # 1. initialize handshake → session id
            resp = _post(
                client,
                headers={"Authorization": f"Bearer {keys['a']}"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
            )
            assert resp.status_code == 200
            session_id = resp.headers.get("mcp-session-id")
            assert session_id, "initialize should mint a session id"

            # 2. tools/call on that session → the tool echoes the principal
            resp = _post(
                client,
                headers={
                    "Authorization": f"Bearer {keys['a']}",
                    "mcp-session-id": session_id,
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "whoami", "arguments": {}},
                },
            )
            assert resp.status_code == 200
            assert "agent-a" in resp.text
            assert "hr" in resp.text and "policy" in resp.text

    def test_scope_carries_authenticated_user_for_session_binding(self, keys) -> None:
        """The mcp-library ``scope["user"]`` must be set so the session
        manager can bind a session to this key."""
        from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

        captured: dict[str, Any] = {}

        async def _inner(scope, receive, send):
            captured["user"] = scope.get("user")
            captured["principal"] = scope.get(SCOPE_PRINCIPAL)
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": b'{"ok":true}'})

        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.routing import Route

        async def _handler(request):
            captured["user"] = request.scope.get("user")
            captured["principal"] = request.scope.get(SCOPE_PRINCIPAL)
            return JSONResponse({"ok": True})

        # A Starlette app that routes a capture handler behind the
        # middleware, so TestClient's lifespan handshake is satisfied.
        app = Starlette(
            middleware=[Middleware(MCPAccessAuthMiddleware, key_service=keys["service"])],
            routes=[Route("/mcp", _handler, methods=["POST"])],
        )
        with TestClient(app) as client:
            resp = _post(
                client,
                headers={"Authorization": f"Bearer {keys['a']}"},
                content=b'{"jsonrpc":"2.0"}',
            )
        assert resp.status_code == 200
        assert isinstance(captured["user"], AuthenticatedUser)
        # client_id of the mcp-library user == the key_id of the credential.
        assert captured["user"].username == keys["a"][len("skdy_mcp_"):].split(".", 1)[0]
        assert captured["principal"] is not None
        assert captured["principal"].name == "agent-a"


# ---------------------------------------------------------------------------
# Fail-closed on broken store
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_broken_store_refuses_to_start(self, tmp_path) -> None:
        class BrokenStore:
            def list_keys(self):
                raise RuntimeError("disk full")

            def authenticate(self, raw_key):
                raise RuntimeError("disk full")

        with pytest.raises(RuntimeError, match="refusing to start"):
            build_asgi_app(_build_test_server(), key_service=BrokenStore())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_asgi_app without a key service stays open
# ---------------------------------------------------------------------------

class TestOpenWhenNoKeyService:
    def test_no_key_service_means_no_auth(self, tmp_path) -> None:
        app = build_asgi_app(_build_test_server(), key_service=None)
        with TestClient(app) as client:
            resp = _post(client, content=b'{"jsonrpc":"2.0"}')
        assert resp.status_code != 401
