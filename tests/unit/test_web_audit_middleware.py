from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.application.services.audit_store import AuditStore
from src.web_api.middleware.audit import AuditMiddleware
from src.web_api.middleware.request_id import RequestIDMiddleware


def _client(tmp_path):
    audit = AuditStore(tmp_path / "audit.db")
    app = FastAPI()
    app.state.application_services = SimpleNamespace(audit=audit)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestIDMiddleware)

    @app.post("/api/v1/collections/{collection_id}/documents/{document_id}")
    def mutate(collection_id: str, document_id: str):
        return {"ok": True}

    @app.get("/api/v1/collections/{collection_id}")
    def denied(collection_id: str):
        from fastapi import Response
        return Response(status_code=403)

    return TestClient(app), audit


def test_mutation_records_template_ids_and_request_id_without_payload(tmp_path) -> None:
    client, audit = _client(tmp_path)
    response = client.post(
        "/api/v1/collections/manuals/documents/doc-1?query=private",
        json={"content": "secret"},
        headers={"Authorization": "Bearer hidden", "X-Request-ID": "req-1"},
    )
    assert response.status_code == 200
    row = audit.list()[0]
    assert row["resource_type"] == "/api/v1/collections/{collection_id}/documents/{document_id}"
    assert row["collection_id"] == "manuals"
    assert row["resource_id"] == "doc-1"
    assert row["request_id"] == "req-1"
    assert "private" not in str(row) and "hidden" not in str(row) and "secret" not in str(row)


def test_permission_denial_is_audited_even_for_read(tmp_path) -> None:
    client, audit = _client(tmp_path)
    assert client.get("/api/v1/collections/blocked").status_code == 403
    assert audit.list()[0]["outcome"] == "denied"
