from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.application.contracts import ChunkRevision, RevisionSource
from src.ingestion.storage import RevisionConflictError
from src.application.services.revision_indexer import (
    RevisionIndexResult,
    RevisionIndexingError,
)
from src.web_api.app import create_app


class FakeRevisionService:
    def __init__(self):
        self.calls = []
        self.error = None

    def create_edit(self, document_id, chunk_id, principal, **kwargs):
        self.calls.append((document_id, chunk_id, principal, kwargs))
        if self.error:
            raise self.error
        return ChunkRevision(
            revision_id="r2", collection="kb", document_id=document_id,
            chunk_id=chunk_id, text=kwargs["text"],
            metadata=kwargs["metadata_patch"],
            base_revision_id=kwargs["base_revision_id"],
            source=RevisionSource.EDIT, reason=kwargs["reason"],
            actor=kwargs["actor"], created_at=datetime.now(timezone.utc),
        )

    def create_rollback(self, document_id, chunk_id, principal, **kwargs):
        self.calls.append((document_id, chunk_id, principal, kwargs))
        if self.error:
            raise self.error
        return ChunkRevision(
            revision_id="r3", collection="kb", document_id=document_id,
            chunk_id=chunk_id, text="restored",
            base_revision_id=kwargs["base_revision_id"],
            source=RevisionSource.ROLLBACK, reason=kwargs["reason"],
            actor=kwargs["actor"],
            rollback_target_revision_id=kwargs["target_revision_id"],
        )


def _client(monkeypatch):
    import src.web_api.routers.revisions as module

    service = FakeRevisionService()
    monkeypatch.setattr(module, "_service", lambda services: service)
    return TestClient(create_app(services=object())), service


def test_edit_requires_matching_if_match_and_creates_pending(monkeypatch):
    client, service = _client(monkeypatch)
    response = client.post(
        "/api/v1/documents/doc/chunks/chunk/revisions",
        headers={"If-Match": '"r1"', "X-Actor-ID": "user:7"},
        json={
            "base_revision_id": "r1", "text": "fixed",
            "metadata_patch": {"section": "Corrected"}, "reason": "typo",
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "pending_index"
    assert response.json()["is_current"] is False
    assert service.calls[0][3]["base_revision_id"] == "r1"


def test_edit_rejects_header_body_mismatch(monkeypatch):
    client, service = _client(monkeypatch)
    response = client.post(
        "/api/v1/documents/doc/chunks/chunk/revisions",
        headers={"If-Match": '"stale"', "X-Actor-ID": "user:7"},
        json={"base_revision_id": "r1", "text": "fixed", "reason": "typo"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert service.calls == []


def test_edit_maps_store_conflict_to_409(monkeypatch):
    client, service = _client(monkeypatch)
    service.error = RevisionConflictError("stale")
    response = client.post(
        "/api/v1/documents/doc/chunks/chunk/revisions",
        headers={"If-Match": "r1", "X-Actor-ID": "user:7"},
        json={"base_revision_id": "r1", "text": "fixed", "reason": "typo"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_edit_requires_actor_and_if_match(monkeypatch):
    client, _ = _client(monkeypatch)
    response = client.post(
        "/api/v1/documents/doc/chunks/chunk/revisions",
        json={"base_revision_id": "r1", "text": "fixed", "reason": "typo"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_rollback_creates_a_new_pending_revision(monkeypatch):
    client, service = _client(monkeypatch)
    response = client.post(
        "/api/v1/documents/doc/chunks/chunk/revisions/r1/rollback",
        headers={"If-Match": '"r2"', "X-Actor-ID": "user:7"},
        json={"base_revision_id": "r2", "reason": "restore known good"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["revision"]["revision_id"] == "r3"
    assert body["revision"]["rollback_target_revision_id"] == "r1"
    assert body["status"] == "pending_index"
    assert service.calls[-1][3]["target_revision_id"] == "r1"


def test_rebuild_activates_all_indexes_and_invalidates_cache(monkeypatch):
    import src.web_api.routers.revisions as module

    revision = ChunkRevision(
        revision_id="r2", collection="kb", document_id="doc", chunk_id="chunk",
        text="fixed", base_revision_id="r1", source=RevisionSource.EDIT,
        reason="fix", actor="user",
    )

    class Store:
        def get(self, revision_id):
            return revision

    class Coordinator:
        def rebuild_and_activate(self, revision_id):
            return RevisionIndexResult(
                revision_id, True, ("dense", "bm25", "parent_child"),
            )

    invalidated = []
    engines = SimpleNamespace(
        invalidate_collection=lambda collection: invalidated.append(collection),
    )
    monkeypatch.setattr(module, "RevisionStore", lambda path: Store())
    monkeypatch.setattr(module, "_coordinator", lambda *args: Coordinator())
    client = TestClient(create_app(services=SimpleNamespace(engines=engines)))
    response = client.post(
        "/api/v1/documents/doc/chunks/chunk/revisions/r2/rebuild",
    )
    assert response.status_code == 200
    assert response.json()["participants"] == ["dense", "bm25", "parent_child"]
    assert invalidated == ["kb"]


def test_rebuild_failure_returns_409_without_cache_invalidation(monkeypatch):
    import src.web_api.routers.revisions as module

    revision = ChunkRevision(
        revision_id="r2", collection="kb", document_id="doc", chunk_id="chunk",
        text="fixed", base_revision_id="r1", source=RevisionSource.EDIT,
        reason="fix", actor="user",
    )

    class Store:
        def get(self, revision_id):
            return revision

    class Coordinator:
        def rebuild_and_activate(self, revision_id):
            raise RevisionIndexingError(RevisionIndexResult(
                revision_id, False, ("dense", "bm25"), "bm25", "OSError",
            ))

    invalidated = []
    engines = SimpleNamespace(
        invalidate_collection=lambda collection: invalidated.append(collection),
    )
    monkeypatch.setattr(module, "RevisionStore", lambda path: Store())
    monkeypatch.setattr(module, "_coordinator", lambda *args: Coordinator())
    client = TestClient(create_app(services=SimpleNamespace(engines=engines)))
    response = client.post(
        "/api/v1/documents/doc/chunks/chunk/revisions/r2/rebuild",
    )
    assert response.status_code == 409
    assert response.json()["error"]["details"]["failed_participant"] == "bm25"
    assert invalidated == []
