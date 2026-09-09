"""Trusted-admin MCP API-key management endpoint coverage."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.mcp_server.auth import ApiKeyService
from src.web_api.app import create_app


def test_create_list_rotate_revoke_and_delete_mcp_key(monkeypatch, tmp_path):
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

        renamed = client.patch("/api/v1/mcp-keys/partner-a", json={"name": "partner-renamed"})
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "partner-renamed"

        updated = client.put(
            "/api/v1/mcp-keys/partner-renamed/collections",
            json={"allowed_collections": ["policy", "finance"]},
        )
        assert updated.status_code == 200
        assert updated.json()["key_id"] == created.json()["key_id"]
        assert updated.json()["allowed_collections"] == ["finance", "policy"]
        principal = service.authenticate(created.json()["api_key"])
        assert principal is not None
        assert principal.allowed_collections == frozenset({"finance", "policy"})

        rotated = client.post("/api/v1/mcp-keys/partner-renamed/rotate")
        assert rotated.status_code == 200
        assert rotated.json()["api_key"] != created.json()["api_key"]

        revoked = client.post("/api/v1/mcp-keys/partner-renamed/revoke")
        assert revoked.status_code == 200
        assert revoked.json()["enabled"] is False

        deleted = client.delete("/api/v1/mcp-keys/partner-renamed")
        assert deleted.status_code == 204
        assert client.get("/api/v1/mcp-keys").json()["items"] == []
