from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.connectors.contracts import ConnectorItem, SourceDocument, SyncCheckpoint
from src.application.services.worker_pool import DurableWorkerPool, DurableWorkerStore
from src.connectors.http_security import UnsafeUrlError
from src.connectors.service import (
    DataSourceDisabledError, DataSourceSyncService, sync_worker_handler,
)
from src.connectors.store import CredentialCipher, DataSourceStore


class FakeConnector:
    def __init__(self, documents: list[SourceDocument], *, fail: Exception | None = None):
        self.documents = {item.external_id: item for item in documents}
        self.fail = fail; self.closed = False; self.tested = False

    def test(self): self.tested = True
    def list(self, checkpoint):
        if self.fail: raise self.fail
        return [ConnectorItem(item.external_id, item.revision, item.deleted) for item in self.documents.values()]
    def fetch(self, item): return self.documents[item.external_id]
    def checkpoint(self):
        return SyncCheckpoint("next", 1, datetime(2026, 9, 18, tzinfo=timezone.utc))
    def close(self): self.closed = True


def _source(store: DataSourceStore):
    return store.create(
        name="feed", connector_type="rss", collection_id="manuals",
        policy={"url": "https://example.com/feed"}, credentials={},
    )


def _doc(external_id: str, *, deleted: bool = False) -> SourceDocument:
    return SourceDocument(
        external_id=external_id, revision="r1", title=external_id,
        content=b"body", media_type="text/plain",
        source_uri="https://example.com/feed", checksum_sha256="hash",
        deleted=deleted,
    )


def test_connection_check_decrypts_builds_and_closes(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = _source(store); connector = FakeConnector([])
    service = DataSourceSyncService(store, connector_builder=lambda *_: connector)
    service.test_connection(source.id)
    assert connector.tested and connector.closed
    assert store.get(source.id).checkpoint_revision == 0


def test_successful_run_applies_all_items_then_advances_checkpoint(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = _source(store); connector = FakeConnector([_doc("a"), _doc("b", deleted=True)])
    service = DataSourceSyncService(store, connector_builder=lambda *_: connector)
    seen = []
    result = service.run(
        source.id,
        lambda _, document: seen.append(document.external_id) or ("deleted" if document.deleted else "added"),
    )
    assert seen == ["a", "b"]
    assert result["status"] == "succeeded"
    assert result["added"] == 1 and result["deleted"] == 1
    refreshed = store.get(source.id)
    assert refreshed.checkpoint_revision == 1
    assert refreshed.checkpoint["cursor"] == "next"
    assert store.sync_status(source.id)["last_run"]["status"] == "succeeded"


def test_failed_run_keeps_checkpoint_and_records_stable_error(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = _source(store); connector = FakeConnector([], fail=TimeoutError("secret detail"))
    service = DataSourceSyncService(store, connector_builder=lambda *_: connector)
    with pytest.raises(TimeoutError):
        service.run(source.id, lambda *_: "added")
    assert store.get(source.id).checkpoint_revision == 0
    run = store.sync_status(source.id)["last_run"]
    assert run["status"] == "failed" and run["error_code"] == "sync_failed"
    assert "secret detail" not in str(run)


def test_paused_source_never_builds_connector_or_creates_run(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = _source(store); store.set_enabled(source.id, False)
    service = DataSourceSyncService(
        store, connector_builder=lambda *_: pytest.fail("must not build"),
    )
    with pytest.raises(DataSourceDisabledError):
        service.run(source.id, lambda *_: "added")
    assert store.sync_status(source.id)["last_run"] is None


def test_sync_worker_dead_letters_ssrf_and_retries_timeout(tmp_path) -> None:
    class FailingService:
        @staticmethod
        def _error_code(exc):
            return "unsafe_url" if isinstance(exc, UnsafeUrlError) else "upstream_timeout"
        def run(self, source_id, apply_document, heartbeat=None):
            if source_id == "unsafe":
                raise UnsafeUrlError("blocked")
            raise TimeoutError("temporary")

    store = DurableWorkerStore(tmp_path / "workers.db")
    handler = sync_worker_handler(FailingService(), lambda *_: "added")
    pool = DurableWorkerPool(store, {"sync": handler}, owner="worker", lease_seconds=10)
    store.enqueue("unsafe-job", "sync", {"source_id": "unsafe"})
    assert pool.run_once("sync")
    assert store.get("unsafe-job")["state"] == "dead_letter"
    assert store.get("unsafe-job")["error_code"] == "unsafe_url"

    store.enqueue("timeout-job", "sync", {"source_id": "timeout"}, max_attempts=2)
    assert pool.run_once("sync")
    assert store.get("timeout-job")["state"] == "queued"
    assert store.get("timeout-job")["error_code"] == "upstream_timeout"
