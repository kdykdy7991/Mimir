from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.application.contracts import ChunkRevision, RevisionSource
from src.ingestion.storage import (
    DerivedContentStore, EnrichmentMetricsStore, EnrichmentStore,
)
from src.application.services.derived_content_service import DerivedContentService
from src.web_api.app import create_app
from src.web_api.mappers import collection_uuid


class LLM:
    model = "test-model"

    def chat(self, messages, **kwargs):
        return '[{"name":"Finance","confidence":0.9}]'


class DB:
    def __init__(self):
        self.manual = ["manual"]
        self.applied = []
        self.tag = {
            "tag_id": "finance", "name": "Finance",
            "collection_id": str(collection_uuid("kb")),
        }

    def list_tags(self, collection_id):
        return [self.tag]

    def document_tag_ids(self, document_id):
        return list(self.manual)

    def get_tag(self, tag_id):
        return self.tag if tag_id == "finance" else None

    def add_document_tag(self, document_id, tag_id):
        self.applied.append((document_id, tag_id))
        return True


def _setup(monkeypatch, tmp_path):
    import src.web_api.routers.enrichment as module

    store = EnrichmentStore(tmp_path / "enrichment.sqlite3")
    revision = ChunkRevision(
        revision_id="r1", collection="kb", document_id="doc", chunk_id="chunk",
        text="financial report", source=RevisionSource.INITIAL,
        reason="initial", actor="system",
    )

    class Revisions:
        def __init__(self, path):
            pass

        def active_for_document_chunk(self, document_id, chunk_id):
            return revision if (document_id, chunk_id) == ("doc", "chunk") else None

    db = DB()
    engines = SimpleNamespace(
        llm=LLM(), settings=SimpleNamespace(tag_enrichment_enabled=True),
    )
    services = SimpleNamespace(db=db, engines=engines)
    monkeypatch.setattr(module, "_store", lambda: store)
    monkeypatch.setattr(
        module, "_metrics_store",
        lambda: EnrichmentMetricsStore(tmp_path / "metrics.sqlite3"),
    )
    monkeypatch.setattr(module, "RevisionStore", Revisions)
    return TestClient(create_app(services=services)), db


def test_generate_list_and_approve_suggestion(monkeypatch, tmp_path):
    client, db = _setup(monkeypatch, tmp_path)
    generated = client.post(
        "/api/v1/documents/doc/chunks/chunk/tag-suggestions",
    )
    assert generated.status_code == 200
    suggestion = generated.json()["suggestions"][0]
    assert suggestion["existing_tag_id"] == "finance"
    listed = client.get("/api/v1/documents/doc/tag-suggestions")
    assert listed.status_code == 200 and listed.json()["count"] == 1
    reviewed = client.post(
        f"/api/v1/tag-suggestions/{suggestion['suggestion_id']}/review",
        headers={"X-Actor-ID": "reviewer:1"}, json={"approve": True},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["suggestion"]["status"] == "approved"
    assert db.manual == ["manual"]
    assert db.applied == [("doc", "finance")]


def test_approval_rejects_cross_collection_or_unknown_tag(monkeypatch, tmp_path):
    client, db = _setup(monkeypatch, tmp_path)
    suggestion = client.post(
        "/api/v1/documents/doc/chunks/chunk/tag-suggestions",
    ).json()["suggestions"][0]
    db.tag["collection_id"] = str(collection_uuid("other"))
    response = client.post(
        f"/api/v1/tag-suggestions/{suggestion['suggestion_id']}/review",
        headers={"X-Actor-ID": "reviewer:1"}, json={"approve": True},
    )
    assert response.status_code == 400
    assert db.applied == []


def test_disabled_feature_returns_no_suggestions(monkeypatch, tmp_path):
    client, _ = _setup(monkeypatch, tmp_path)
    app_services = client.app.state.application_services
    app_services.engines.settings.tag_enrichment_enabled = False
    response = client.post(
        "/api/v1/documents/doc/chunks/chunk/tag-suggestions",
    )
    assert response.status_code == 200 and response.json()["count"] == 0


def test_derived_artifacts_can_be_listed_disabled_and_deleted(monkeypatch, tmp_path):
    import src.web_api.routers.enrichment as module

    client, _ = _setup(monkeypatch, tmp_path)
    store = DerivedContentStore(tmp_path / "derived.sqlite3")
    service = DerivedContentService(
        store, lambda text: {"summary": "S", "questions": ["Q?"]},
        model="m", prompt_version="p", enabled=True,
    )
    from src.application.contracts import ChunkRevision, RevisionSource
    revision = ChunkRevision(
        revision_id="r1", collection="kb", document_id="doc", chunk_id="chunk",
        text="source", source=RevisionSource.INITIAL,
        reason="initial", actor="system",
    )
    artifacts = service.rebuild(revision)
    monkeypatch.setattr(module, "_derived_store", lambda: store)
    listed = client.get("/api/v1/revisions/r1/derived-artifacts")
    assert listed.status_code == 200 and listed.json()["count"] == 2
    changed = client.patch(
        f"/api/v1/derived-artifacts/{artifacts[0].artifact_id}",
        json={"enabled": False},
    )
    assert changed.status_code == 200 and changed.json()["enabled"] is False
    deleted = client.delete("/api/v1/revisions/r1/derived-artifacts")
    assert deleted.status_code == 200 and deleted.json()["deleted"] == 2
