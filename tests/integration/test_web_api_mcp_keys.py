"""Trusted-admin MCP API-key management endpoint coverage."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.mcp_server.auth import ApiKeyService
from src.web_api.app import create_app


def test_create_list_rotate_and_revoke_mcp_key(monkeypatch, tmp_path):
    from src.web_api.routers import mcp_keys

    service = ApiKeyService(db_path=tmp_path / "mcp_access.db")
    monkeypatch.setattr(mcp_keys, "_service", lambda: service)
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/v1/mcp-keys",
            json={"name": "partner-a", "allowed_collections": ["hr"]},
        )
        assert created.status_code == 201
        assert created.json()["api_key"].startswith("skdy_mcp_")

        listed = client.get("/api/v1/mcp-keys")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["name"] == "partner-a"
        assert "api_key" not in listed.json()["items"][0]

        rotated = client.post("/api/v1/mcp-keys/partner-a/rotate")
        assert rotated.status_code == 200
        assert rotated.json()["api_key"] != created.json()["api_key"]

        revoked = client.post("/api/v1/mcp-keys/partner-a/revoke")
        assert revoked.status_code == 200
        assert revoked.json()["enabled"] is False
