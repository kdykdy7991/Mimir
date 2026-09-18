from __future__ import annotations

import pytest

import httpx

from src.connectors.conflicts import RevisionAwareDocumentApplier, SyncConflictStore
from src.connectors.contracts import SourceDocument
from src.connectors.http_security import SafeHttpClient
from src.connectors.rss_url import ControlledUrlConnector, RssConnector


PUBLIC = lambda host, port: ("93.184.216.34",)


def client(handler):
    return SafeHttpClient(resolver=PUBLIC, transport=httpx.MockTransport(handler))


def test_controlled_url_uses_conditional_headers_and_content_hash() -> None:
    calls = []
    def handler(request):
        calls.append(dict(request.headers))
        if len(calls) == 2:
            return httpx.Response(304)
        return httpx.Response(200, headers={"etag": '"v1"', "content-type": "text/plain"}, content=b"hello")
    connector = ControlledUrlConnector("https://public.example/a.txt", client(handler))
    first = tuple(connector.list(None))
    assert len(first) == 1 and connector.fetch(first[0]).content == b"hello"
    checkpoint = connector.checkpoint()
    assert tuple(connector.list(checkpoint)) == ()
    assert calls[1]["if-none-match"] == '"v1"'


def test_rss_incremental_update_and_remote_delete_tombstone() -> None:
    feeds = [
        b"<rss><channel><item><guid>a</guid><title>A</title><description>one</description></item><item><guid>b</guid><title>B</title><description>two</description></item></channel></rss>",
        b"<rss><channel><item><guid>a</guid><title>A</title><description>changed</description></item></channel></rss>",
    ]
    connector = RssConnector(
        "https://public.example/feed.xml",
        client(lambda request: httpx.Response(200, content=feeds.pop(0))),
    )
    initial = tuple(connector.list(None))
    assert len(initial) == 2
    checkpoint = connector.checkpoint()
    changed = tuple(connector.list(checkpoint))
    assert len(changed) == 2
    assert sum(item.deleted for item in changed) == 1
    live = next(item for item in changed if not item.deleted)
    assert connector.fetch(live).content == b"changed"


def test_manual_revision_creates_conflict_without_calling_writer(tmp_path) -> None:
    store = SyncConflictStore(tmp_path / "sync.db")
    store.bind("source-1", "external-1", "doc-1", "sync-r1")
    document = SourceDocument(
        external_id="external-1", revision="remote-r2", title="A",
        content=b"changed", media_type="text/plain", source_uri="https://public.example/a",
        checksum_sha256="hash",
    )
    written = []
    outcome, conflict_id = store.apply_remote(
        source_id="source-1", document_id="doc-1", active_revision_id="manual-r2",
        document=document, create_revision=lambda doc: written.append(doc) or "sync-r2",
    )
    assert outcome == "conflict" and conflict_id
    assert written == []
    assert store.list_pending("source-1")[0]["remote_revision"] == "remote-r2"
    resolved = store.acknowledge("source-1", conflict_id)
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == "manually_resolved"
    assert store.list_pending("source-1") == []
    assert store.acknowledge("source-1", conflict_id)["id"] == conflict_id


def test_unchanged_active_revision_generates_new_sync_revision(tmp_path) -> None:
    store = SyncConflictStore(tmp_path / "sync.db")
    store.bind("source-1", "external-1", "doc-1", "sync-r1")
    document = SourceDocument(
        external_id="external-1", revision="remote-r2", title="A",
        content=b"changed", media_type="text/plain", source_uri="https://public.example/a",
        checksum_sha256="hash",
    )
    outcome, revision = store.apply_remote(
        source_id="source-1", document_id="doc-1", active_revision_id="sync-r1",
        document=document, create_revision=lambda doc: "sync-r2",
    )
    assert (outcome, revision) == ("applied", "sync-r2")
    assert store.list_pending("source-1") == []


def test_revision_aware_applier_commits_binding_only_after_writer(tmp_path) -> None:
    store = SyncConflictStore(tmp_path / "sync.db")
    source = type("Source", (), {"id": "source-1", "collection_id": "manuals"})()
    document = SourceDocument(
        external_id="external-1", revision="remote-r1", title="A",
        content=b"body", media_type="text/plain",
        source_uri="https://public.example/a", checksum_sha256="hash",
    )
    written = []
    applier = RevisionAwareDocumentApplier(
        store, lambda source, doc: None,
        lambda source, document_id, doc: written.append((source, document_id, doc)) or "sync-r1",
    )
    assert applier(source, document) == "added"
    assert written[0][0] is source
    assert written[0][2] is document
    assert store.binding("source-1", "external-1")["last_synced_revision_id"] == "sync-r1"


def test_revision_aware_applier_never_calls_writer_over_manual_revision(tmp_path) -> None:
    store = SyncConflictStore(tmp_path / "sync.db")
    store.bind("source-1", "external-1", "doc-1", "sync-r1")
    source = type("Source", (), {"id": "source-1", "collection_id": "manuals"})()
    document = SourceDocument(
        external_id="external-1", revision="remote-r2", title="A",
        content=b"changed", media_type="text/plain",
        source_uri="https://public.example/a", checksum_sha256="hash2",
    )
    applier = RevisionAwareDocumentApplier(
        store, lambda source, doc: ("doc-1", "manual-r2"),
        lambda *_: pytest.fail("manual active must not be overwritten"),
    )
    assert applier(source, document) == "conflict"
    assert store.binding("source-1", "external-1")["last_synced_revision_id"] == "sync-r1"


def test_revision_aware_applier_writer_failure_leaves_binding_absent(tmp_path) -> None:
    store = SyncConflictStore(tmp_path / "sync.db")
    source = type("Source", (), {"id": "source-1", "collection_id": "manuals"})()
    document = SourceDocument(
        external_id="external-1", revision="remote-r1", title="A",
        content=b"body", media_type="text/plain",
        source_uri="https://public.example/a", checksum_sha256="hash",
    )
    applier = RevisionAwareDocumentApplier(
        store, lambda source, doc: None,
        lambda *_: (_ for _ in ()).throw(RuntimeError("index failed")),
    )
    with pytest.raises(RuntimeError, match="index failed"):
        applier(source, document)
    assert store.binding("source-1", "external-1") is None


def test_revision_aware_applier_allows_recreate_after_synced_deletion(tmp_path) -> None:
    store = SyncConflictStore(tmp_path / "sync.db")
    store.bind("source-1", "external-1", "doc-1", "deleted:remote-r2")
    source = type("Source", (), {"id": "source-1", "collection_id": "manuals"})()
    document = SourceDocument(
        external_id="external-1", revision="remote-r3", title="Restored",
        content=b"restored", media_type="text/plain",
        source_uri="https://public.example/a", checksum_sha256="hash3",
    )
    applier = RevisionAwareDocumentApplier(
        store, lambda source, doc: None, lambda *_: "sync-r3",
    )

    assert applier(source, document) == "updated"
    assert store.list_pending("source-1") == []
    assert store.binding("source-1", "external-1")["last_synced_revision_id"] == "sync-r3"
