"""
Integration tests for HTTP MCP access control (PRD §11.2).

Drives the full chain over ``TestClient``:

    Bearer API key → MCPAccessAuthMiddleware → scope principal →
    ProtocolHandler dispatch → tool authorization

without spinning up real vector stores: the tools are registered on a
real :class:`ProtocolHandler` but their handlers only exercise the
authorization helpers (:mod:`src.mcp_server.auth.authorization`), which
is the part the PRD assigns to the MCP surface. This keeps the test fast
and deterministic while still covering:

1. No key cannot initialize.
2. Agent A (hr,policy) lists only hr + policy.
3. A can query hr, cannot query finance.
4. A omitting collection (two grants) gets a tool-level error.
5. Agent B (finance) omitting collection auto-selects finance.
6. A cannot read finance document summaries; response hides existence.
7. A revoked key cannot initialize / continue.
8. B presenting A's session id → 403 (native session-owner binding).
9. Two keys in parallel do not cross identities (principal isolation).
10. stdio semantics stay intact (TrustedLocalPrincipal) via direct
    dispatch with no HTTP principal.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient

from src.mcp_server.auth import ApiKeyService
from src.mcp_server.auth.authorization import (
    require_collection_access,
    resolve_query_collection,
)
from src.mcp_server.auth.context import TrustedLocalPrincipal, current_principal
from src.mcp_server.auth.models import AccessPrincipal
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error
from src.mcp_server.transports.streamable_http import build_asgi_app


# ---------------------------------------------------------------------------
# Fixtures: keys + a ProtocolHandler with authorization-exercising tools
# ---------------------------------------------------------------------------

@pytest.fixture
def access(tmp_path):
    """Keys for agent-a / agent-b / revoked, plus the app."""
    service = ApiKeyService(db_path=tmp_path / "mcp_access.db")
    raw_a, _ = service.create_key(
        name="agent-a", allowed_collections={"hr", "policy"},
    )
    raw_b, _ = service.create_key(
        name="agent-b", allowed_collections={"finance"},
    )
    raw_revoked, _ = service.create_key(
        name="revoked", allowed_collections={"hr"},
    )
    service.revoke_key(name="revoked")
    return {"service": service, "a": raw_a, "b": raw_b, "revoked": raw_revoked}


def _whoami() -> str:
    """Echo the current principal's identity (single collection)."""
    p = current_principal()
    if p.allowed_collections is None:  # TrustedLocalPrincipal (stdio)
        return f"{p.name}:*"
    return f"{p.name}:{','.join(sorted(p.allowed_collections))}"


async def _query_handler(args: dict[str, Any]) -> Any:
    """Mirror query_knowledge_hub's collection resolution (§6.1)."""
    try:
        collection = resolve_query_collection(current_principal(), args.get("collection"))
    except Exception as exc:
        return tool_error(str(exc))
    return f"query:{collection}:{_whoami()}"


async def _list_handler(args: dict[str, Any]) -> Any:
    """Mirror list_collections's filter (§6.2)."""
    from src.mcp_server.auth.authorization import filter_accessible_collections
    server_collections = ["finance", "hr", "policy"]
    visible = filter_accessible_collections(current_principal(), server_collections)
    return f"list:{','.join(visible)}"


async def _summary_handler(args: dict[str, Any]) -> Any:
    """Mirror get_document_summary's anti-probe behavior (§6.3)."""
    doc_id = args.get("doc_id", "")
    collection = args.get("collection")
    if doc_id not in ("doc-hr-1", "doc-finance-1"):
        return tool_error("document not found or not accessible")
    try:
        require_collection_access(current_principal(), collection)
    except Exception:
        return tool_error("document not found or not accessible")
    return f"summary:{collection}:{_whoami()}"


def _build_handler() -> ProtocolHandler:
    handler = ProtocolHandler()
    handler.register(
        name="query", description="query a collection",
        input_schema={"type": "object", "properties": {"collection": {"type": "string"}}},
        handler=_query_handler,
    )
    handler.register(
        name="list", description="list collections",
        input_schema={"type": "object", "properties": {}},
        handler=_list_handler,
    )
    handler.register(
        name="summary", description="get document summary",
        input_schema={"type": "object", "properties": {
            "doc_id": {"type": "string"}, "collection": {"type": "string"},
        }},
        handler=_summary_handler,
    )
    return handler


@pytest.fixture
def app(access, tmp_path):
    server = _build_handler().build_server()
    return build_asgi_app(server, key_service=access["service"])


# ---------------------------------------------------------------------------
# JSON-RPC helpers over TestClient
# ---------------------------------------------------------------------------

def _rpc(client: TestClient, method: str, params: dict | None = None,
         *, session_id: str | None = None, auth: str | None = None):
    headers = {}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    if session_id:
        headers["mcp-session-id"] = session_id
    return client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def _initialize(client: TestClient, *, auth: str) -> tuple[int, str | None, Any]:
    """Return (status, session_id, body)."""
    resp = _rpc(
        client, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "it", "version": "1.0"},
        },
        auth=auth,
    )
    return resp.status_code, resp.headers.get("mcp-session-id"), resp


def _call(client: TestClient, name: str, args: dict, *, auth: str,
          session_id: str | None) -> tuple[int, Any]:
    resp = _rpc(
        client, "tools/call",
        {"name": name, "arguments": args},
        auth=auth, session_id=session_id,
    )
    return resp.status_code, resp


def _rpc_json(resp: Any) -> dict[str, Any]:
    """Parse a response body that may be plain JSON or SSE-framed.

    The transport defaults to SSE (``json_response=False``), so a
    ``tools/call`` reply arrives as ``event: message\\ndata: {...}``.
    """
    text = resp.text
    if text.startswith("event:"):
        data = [ln[len("data: "):] for ln in text.splitlines()
                if ln.startswith("data: ")]
        # The last JSON-RPC frame is the response.
        payload = json.loads(data[-1])
    else:
        payload = resp.json()
    return payload


def _tool_text(resp: Any) -> str:
    """Extract the first text content from a tools/call result."""
    result = _rpc_json(resp).get("result", {})
    content = result.get("content", [])
    for block in content:
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _is_error(resp: Any) -> bool:
    return _rpc_json(resp).get("result", {}).get("isError", False)


# ---------------------------------------------------------------------------
# 1-7: auth + per-key authorization
# ---------------------------------------------------------------------------

class TestAccessControl:
    def test_no_key_cannot_initialize(self, app) -> None:
        with TestClient(app) as client:
            status, _sid, _ = _initialize(client, auth="")
        assert status == 401

    def test_revoked_key_cannot_initialize(self, app, access) -> None:
        with TestClient(app) as client:
            status, _sid, _ = _initialize(client, auth=access["revoked"])
        assert status == 401

    def test_wrong_secret_cannot_initialize(self, app, access) -> None:
        key_id = access["a"][len("skdy_mcp_"):].split(".", 1)[0]
        with TestClient(app) as client:
            status, _sid, _ = _initialize(
                client, auth=f"skdy_mcp_{key_id}.wrong",
            )
        assert status == 401

    def test_a_lists_only_its_collections(self, app, access) -> None:
        with TestClient(app) as client:
            status, sid, _ = _initialize(client, auth=access["a"])
            assert status == 200 and sid
            status, resp = _call(client, "list", {}, auth=access["a"], session_id=sid)
        assert status == 200
        assert _tool_text(resp) == "list:hr,policy"

    def test_b_lists_only_its_collection(self, app, access) -> None:
        with TestClient(app) as client:
            status, sid, _ = _initialize(client, auth=access["b"])
            status, resp = _call(client, "list", {}, auth=access["b"], session_id=sid)
        assert _tool_text(resp) == "list:finance"

    def test_a_can_query_allowed_collection(self, app, access) -> None:
        with TestClient(app) as client:
            status, sid, _ = _initialize(client, auth=access["a"])
            status, resp = _call(
                client, "query", {"collection": "hr"}, auth=access["a"], session_id=sid,
            )
        assert status == 200
        assert _tool_text(resp) == "query:hr:agent-a:hr,policy"

    def test_a_cannot_query_forbidden_collection(self, app, access) -> None:
        with TestClient(app) as client:
            status, sid, _ = _initialize(client, auth=access["a"])
            status, resp = _call(
                client, "query", {"collection": "finance"}, auth=access["a"], session_id=sid,
            )
        assert status == 200
        assert _is_error(resp)
        assert "not accessible" in _tool_text(resp)

    def test_a_multi_collection_omitting_collection_errors(self, app, access) -> None:
        with TestClient(app) as client:
            status, sid, _ = _initialize(client, auth=access["a"])
            status, resp = _call(client, "query", {}, auth=access["a"], session_id=sid)
        assert status == 200
        assert _is_error(resp)
        assert "collection" in _tool_text(resp).lower() and "required" in _tool_text(resp)

    def test_b_single_collection_omitting_collection_auto_selects(self, app, access) -> None:
        with TestClient(app) as client:
            status, sid, _ = _initialize(client, auth=access["b"])
            status, resp = _call(client, "query", {}, auth=access["b"], session_id=sid)
        assert status == 200
        assert _tool_text(resp) == "query:finance:agent-b:finance"

    def test_a_summary_for_forbidden_doc_hides_existence(self, app, access) -> None:
        with TestClient(app) as client:
            status, sid, _ = _initialize(client, auth=access["a"])
            status, resp = _call(
                client, "summary", {"doc_id": "doc-finance-1", "collection": "finance"},
                auth=access["a"], session_id=sid,
            )
        assert status == 200
        assert _is_error(resp)
        assert _tool_text(resp) == "document not found or not accessible"

    def test_a_summary_for_allowed_doc_succeeds(self, app, access) -> None:
        with TestClient(app) as client:
            status, sid, _ = _initialize(client, auth=access["a"])
            status, resp = _call(
                client, "summary", {"doc_id": "doc-hr-1", "collection": "hr"},
                auth=access["a"], session_id=sid,
            )
        assert status == 200
        assert not _is_error(resp)
        assert _tool_text(resp) == "summary:hr:agent-a:hr,policy"

    def test_revoked_key_cannot_reuse_session(self, app, access) -> None:
        """A key revoked mid-session: the next request using its (now
        revoked) credential is rejected."""
        with TestClient(app) as client:
            status, sid, _ = _initialize(client, auth=access["a"])
            assert status == 200 and sid
            # Revoke agent-a after its session was created.
            access["service"].revoke_key(name="agent-a")
            status, _resp = _call(
                client, "list", {}, auth=access["a"], session_id=sid,
            )
        assert status == 401


# ---------------------------------------------------------------------------
# 8: cross-key session reuse → 403
# ---------------------------------------------------------------------------

class TestSessionBinding:
    def test_b_presenting_a_session_id_is_rejected(self, app, access) -> None:
        with TestClient(app) as client:
            status, sid, _ = _initialize(client, auth=access["a"])
            assert status == 200 and sid
            # B presents A's session id with B's own (valid) key.
            status, resp = _call(
                client, "list", {}, auth=access["b"], session_id=sid,
            )
        assert status in (403, 404)  # native session-owner mismatch


# ---------------------------------------------------------------------------
# 9: concurrent identity isolation
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_two_keys_do_not_cross_identities(self, app, access) -> None:
        import threading
        results: list[str] = []
        errors: list[Exception] = []

        # One shared TestClient: the session manager's run() may only be
        # entered once per app instance, so the threads multiplex over a
        # single client (and a single manager) rather than each entering
        # the lifespan.
        client = TestClient(app)
        client.__enter__()

        def run(raw_key: str, collection: str, expected: str) -> None:
            try:
                status, sid, _ = _initialize(client, auth=raw_key)
                assert status == 200 and sid
                for _ in range(10):
                    status, resp = _call(
                        client, "query", {"collection": collection},
                        auth=raw_key, session_id=sid,
                    )
                    text = _tool_text(resp)
                    if expected not in text:
                        errors.append(AssertionError(f"expected {expected}, got {text}"))
                        return
                results.append(expected)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        try:
            threads = [
                threading.Thread(target=run, args=(access["a"], "hr", "agent-a")),
                threading.Thread(target=run, args=(access["b"], "finance", "agent-b")),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
        finally:
            client.__exit__(None, None, None)
        assert not errors, errors[0]
        assert sorted(results) == ["agent-a", "agent-b"]


# ---------------------------------------------------------------------------
# 10: stdio / direct dispatch keeps legacy behavior
# ---------------------------------------------------------------------------

class TestStdioCompat:
    async def test_trusted_local_principal_resolves_legacy_default(self) -> None:
        """Without an HTTP principal, stdio falls back to the
        TrustedLocalPrincipal: the legacy ``default`` collection and full
        access to every collection (PRD §7)."""
        principal = TrustedLocalPrincipal()
        assert resolve_query_collection(principal, None) == "default"
        # Full access: a forbidden-by-a-real-key collection is allowed.
        require_collection_access(principal, "finance")
        require_collection_access(principal, "hr")

    async def test_direct_dispatch_with_trusted_principal(self) -> None:
        """ProtocolHandler.dispatch with an explicit stdio principal
        reaches the tool; querying any collection is allowed."""
        handler = _build_handler()
        principal = TrustedLocalPrincipal()
        result = await handler.dispatch(
            "query", {"collection": "finance"}, principal=principal,
        )
        assert isinstance(result, list)
        # The tool's resolve_query_collection granted finance because the
        # trusted principal may read everything.
        assert "query:finance" in result[0].text
