from __future__ import annotations

from fastapi.testclient import TestClient

from src.connectors.conflicts import SyncConflictStore
from src.connectors.contracts import SourceDocument
from src.connectors.store import CredentialCipher, DataSourceStore
from src.application.services.worker_pool import DurableWorkerStore
from src.web_api.app import create_app
from src.web_api.routers.data_sources import (
    _conflict_store, _store, _sync_service, _worker_store,
)


def test_management_api_never_returns_credentials_and_exposes_status(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    app = create_app()
    app.dependency_overrides[_store] = lambda: store
    client = TestClient(app)
    created = client.post("/api/v1/data-sources", json={
        "name": "release feed", "connector_type": "rss",
        "collection_id": "manuals", "policy": {"url": "https://example.com/feed"},
        "credentials": {"password": "never-return"},
    })
    assert created.status_code == 201
    source_id = created.json()["id"]
    assert "credentials" not in created.text and "never-return" not in created.text
    store.record_run(
        source_id=source_id, status="failed", started_at=1, finished_at=2,
        error_code="upstream_timeout",
    )
    status = client.get(f"/api/v1/data-sources/{source_id}/status")
    assert status.json()["last_run"]["error_code"] == "upstream_timeout"
    failures = client.get(f"/api/v1/data-sources/{source_id}/failures")
    assert failures.json()["count"] == 1
    assert client.post(f"/api/v1/data-sources/{source_id}/pause").json()["enabled"] is False


def test_unknown_datasource_is_same_shape_404(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    app = create_app(); app.dependency_overrides[_store] = lambda: store
    response = TestClient(app).get("/api/v1/data-sources/missing/status")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_management_api_lists_and_acknowledges_manual_conflicts(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = store.create(
        name="feed", connector_type="rss", collection_id="manuals",
        policy={"url": "https://example.com/feed"}, credentials={},
    )
    conflicts = SyncConflictStore(tmp_path / "conflicts.db")
    conflicts.bind(source.id, "remote-1", "doc-1", "sync-r1")
    _, conflict_id = conflicts.apply_remote(
        source_id=source.id, document_id="doc-1", active_revision_id="manual-r2",
        document=SourceDocument(
            external_id="remote-1", revision="remote-r2", title="Changed",
            content=b"remote", media_type="text/plain",
            source_uri="https://example.com/feed", checksum_sha256="hash",
        ),
        create_revision=lambda _: "must-not-run",
    )
    app = create_app()
    app.dependency_overrides[_store] = lambda: store
    app.dependency_overrides[_conflict_store] = lambda: conflicts
    client = TestClient(app)

    listed = client.get(f"/api/v1/data-sources/{source.id}/conflicts")
    assert listed.status_code == 200
    assert listed.json()["conflicts"][0]["id"] == conflict_id
    assert "remote_metadata_json" not in listed.text

    acknowledged = client.post(
        f"/api/v1/data-sources/{source.id}/conflicts/{conflict_id}/acknowledge",
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["resolution"] == "manually_resolved"
    assert client.get(f"/api/v1/data-sources/{source.id}/conflicts").json()["count"] == 0


def test_management_api_exposes_connection_check_without_credentials(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = store.create(
        name="feed", connector_type="rss", collection_id="manuals",
        policy={"url": "https://example.com/feed"}, credentials={"token": "hidden"},
    )

    class FakeSyncService:
        def __init__(self): self.called = []
        def test_connection(self, source_id): self.called.append(source_id)

    service = FakeSyncService(); app = create_app()
    app.dependency_overrides[_store] = lambda: store
    app.dependency_overrides[_sync_service] = lambda: service
    response = TestClient(app).post(f"/api/v1/data-sources/{source.id}/test")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "data_source_id": source.id}
    assert service.called == [source.id]
    assert "hidden" not in response.text


def test_management_api_enqueues_idempotent_sync_without_running_inline(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = store.create(
        name="feed", connector_type="rss", collection_id="manuals",
        policy={"url": "https://example.com/feed"}, credentials={},
    )
    workers = DurableWorkerStore(tmp_path / "workers.db")
    app = create_app()
    app.dependency_overrides[_store] = lambda: store
    app.dependency_overrides[_worker_store] = lambda: workers
    client = TestClient(app)

    first = client.post(f"/api/v1/data-sources/{source.id}/sync")
    second = client.post(f"/api/v1/data-sources/{source.id}/sync")

    assert first.status_code == second.status_code == 202
    assert first.json()["enqueued"] is True
    assert second.json() == {"task_id": first.json()["task_id"], "enqueued": False}
    queued = workers.get(first.json()["task_id"])
    assert queued["state"] == "queued" and source.id in queued["payload_json"]


def test_connection_check_maps_policy_failure_without_leaking_url(tmp_path) -> None:
    from src.connectors.http_security import UnsafeUrlError
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = store.create(
        name="blocked", connector_type="url", collection_id="manuals",
        policy={"url": "https://secret.internal/path"}, credentials={},
    )
    class FailingService:
        def test_connection(self, source_id):
            raise UnsafeUrlError("URL resolves to 127.0.0.1 at secret.internal")
    app = create_app(); app.dependency_overrides[_store] = lambda: store
    app.dependency_overrides[_sync_service] = lambda: FailingService()
    response = TestClient(app).post(f"/api/v1/data-sources/{source.id}/test")
    assert response.status_code == 400
    assert response.json()["error"]["details"] == {"reason": "unsafe_url"}
    assert "secret.internal" not in response.text and "127.0.0.1" not in response.text


def test_management_api_lists_and_replays_only_matching_sync_dead_letters(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = store.create(
        name="feed", connector_type="rss", collection_id="manuals",
        policy={"url": "https://example.com/feed"}, credentials={},
    )
    workers = DurableWorkerStore(tmp_path / "workers.db")
    workers.enqueue("sync-dead", "sync", {"source_id": source.id}, max_attempts=1)
    workers.enqueue("other-dead", "sync", {"source_id": "other"}, max_attempts=1)
    for task_id in ("sync-dead", "other-dead"):
        job = workers.claim("sync", "worker", lease_seconds=10)
        assert job is not None
        workers.fail(
            job.task_id, "worker", error_class="permanent",
            error_code="unsafe_url", retryable=False,
        )
    app = create_app(); app.dependency_overrides[_store] = lambda: store
    app.dependency_overrides[_worker_store] = lambda: workers
    client = TestClient(app)

    listed = client.get(f"/api/v1/data-sources/{source.id}/dead-letters")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    item = listed.json()["dead_letters"][0]
    assert item["task_id"] == "sync-dead" and "payload_json" not in item

    replayed = client.post(
        f"/api/v1/data-sources/{source.id}/dead-letters/sync-dead/replay",
    )
    assert replayed.json() == {"task_id": "sync-dead", "replayed": True}
    assert workers.get("sync-dead")["state"] == "queued"
