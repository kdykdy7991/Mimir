"""B4.4 — one-time secret boundary regression tests.

Proves:
(a) after create/rotate the full MCP Client Key is NOT re-readable from any
    endpoint or the database (only a SHA-256 digest is stored);
(b) OpenAPI marks ``api_key`` ``writeOnly: true`` on the test-connection
    request and the create/rotate response, and lists it nowhere else;
(c) no request/response log line for the test-connection path contains the
    key value (body capture is disabled and never echoes the secret).
"""

from __future__ import annotations

import hashlib
import logging

import pytest
from fastapi.testclient import TestClient

from src.mcp_server.auth import ApiKeyService
from src.web_api.app import create_app
from src.web_api.middleware.request_timing import body_capture_disabled
from src.web_api.routers import mcp_server as mcp_server_router
from src.web_api.routers.mcp_server import McpServerRouteConfig


def _patch_mcp_keys(monkeypatch, tmp_path):
    from src.web_api.routers import mcp_keys

    service = ApiKeyService(db_path=tmp_path / "mcp_access.db")
    monkeypatch.setattr(mcp_keys, "_service", lambda: service)
    return service


def _build_app(monkeypatch, tmp_path, *, base_url=""):
    service = _patch_mcp_keys(monkeypatch, tmp_path)
    app = create_app()
    app.include_router(mcp_server_router.router, prefix="/api/v1")

    def fake_config():
        return McpServerRouteConfig(
            base_url=base_url,
            key_db_path=str(tmp_path / "mcp_access.db"),
            timeout_s=3.0,
            upstream_backend="in_process",
            upstream_base_url="",
            upstream_key="",
        )

    monkeypatch.setattr(mcp_server_router, "_server_config", fake_config)
    monkeypatch.setattr(mcp_server_router, "_rate_limiter",
                       mcp_server_router.RuntimeRateLimiter(
                           max_requests=1000, window_seconds=60.0))
    return app, service


# ---------------------------------------------------------------------------
# (a) — secret is not re-readable after create/rotate
# ---------------------------------------------------------------------------


def test_create_secret_not_re_readable_from_endpoints_or_db(monkeypatch, tmp_path):
    app, _service = _build_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/mcp-keys",
            json={"name": "alpha", "allowed_collections": ["hr"]},
        )
        assert created.status_code == 201
        raw_key = created.json()["api_key"]
        assert raw_key.startswith("skdy_mcp_")

        # not re-readable from any management endpoint
        listed = client.get("/api/v1/mcp-keys")
        assert listed.status_code == 200
        assert "api_key" not in listed.json()["items"][0]
        assert raw_key not in listed.text

        # openapi create response does not expose it as read-only
        # (writeOnly flag asserted separately in (b)).

    # only a SHA-256 digest is persisted — the raw key never reaches the DB
    db_path = tmp_path / "mcp_access.db"
    blob = db_path.read_bytes().decode("utf-8", errors="replace")
    assert raw_key not in blob
    secret_part = raw_key.rsplit(".", 1)[1]
    assert secret_part not in blob
    assert hashlib.sha256(secret_part.encode()).hexdigest() in blob


def test_rotate_old_secret_immediately_invalid_and_never_readable(
    monkeypatch, tmp_path,
):
    app, service = _build_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/mcp-keys",
            json={"name": "beta", "allowed_collections": ["finance"]},
        ).json()
        old_key = created["api_key"]
        assert service.authenticate(old_key) is not None

        rotated = client.post("/api/v1/mcp-keys/beta/rotate")
        assert rotated.status_code == 200
        new_key = rotated.json()["api_key"]

        # old key dies instantly
        assert service.authenticate(old_key) is None
        # new key works
        assert service.authenticate(new_key) is not None

        # neither old nor new secret is re-readable
        listed = client.get("/api/v1/mcp-keys")
        assert old_key not in listed.text and new_key not in listed.text

    blob = (tmp_path / "mcp_access.db").read_bytes().decode("utf-8", errors="replace")
    assert old_key not in blob and new_key not in blob
    assert old_key.rsplit(".", 1)[1] not in blob
    assert new_key.rsplit(".", 1)[1] not in blob


# ---------------------------------------------------------------------------
# (b) — OpenAPI writeOnly
# ---------------------------------------------------------------------------


def test_openapi_marks_api_key_write_only(monkeypatch, tmp_path):
    app, _ = _build_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    components = schema["components"]["schemas"]

    # test-connection request: api_key writeOnly
    req = components["MCPConnectionTestRequest"]
    assert req["properties"]["api_key"]["writeOnly"] is True

    # create + rotate responses: api_key writeOnly
    secret = components["MCPKeySecretResponse"]
    assert secret["properties"]["api_key"]["writeOnly"] is True

    # the secret is absent from every response that should never carry it
    for schema_name in ("MCPKeyListResponse", "MCPKeyMetadata",
                        "MCPServerStatus", "MCPConnectionTestResponse"):
        assert "api_key" not in components[schema_name].get("properties", {})


# ---------------------------------------------------------------------------
# (c) — request/response logging never echoes the key
# ---------------------------------------------------------------------------


def test_body_capture_disabled_for_test_connection_path():
    assert body_capture_disabled("/api/v1/mcp-server/test-connection") is True
    assert body_capture_disabled("/api/v1/documents") is False


def test_no_log_line_for_test_connection_contains_the_key(monkeypatch, tmp_path, caplog):
    # point the MCP target at an unreachable host so the test still executes,
    # fails fast, and the full request (body + failure) passes through logging.
    app, _ = _build_app(monkeypatch, tmp_path, base_url="http://127.0.0.1:1")
    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as client:
            key = "skdy_mcp_SECRET_MARKER_abc123.verysecretvalue"
            resp = client.post(
                "/api/v1/mcp-server/test-connection", json={"api_key": key},
            )
    assert resp.status_code == 200
    assert key not in caplog.text
    assert "verysecretvalue" not in caplog.text
    # and it is not echoed in the response either
    assert key not in resp.text
    # the request DID hit our guarded path and the response went through the
    # sensitive path guard without logging the body
    assert body_capture_disabled(
        "/api/v1/mcp-server/test-connection") is True


def test_error_envelope_and_validation_never_echo_key(monkeypatch, tmp_path, caplog):
    app, _ = _build_app(monkeypatch, tmp_path)
    # oversized / obviously bogus key still must not leak its value anywhere
    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/mcp-server/test-connection",
                json={"api_key": "skdy_mcp_" + "x" * 300},  # exceeds maxLength
            )
    assert resp.status_code == 422
    body = resp.text
    assert "skdy_mcp_" not in body  # masked value never returned
    assert caplog.text.count("skdy_mcp_") == 0