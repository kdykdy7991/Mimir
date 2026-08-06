"""
M2 batch 1 — real ``/system``, ``/collections``, ``/documents`` endpoints.

These exercise the FastAPI layer against the application services backed
by the in-memory store fakes from ``test_document_manager``, so no real
disk / Chroma / SQLite is touched. ``DocumentManager`` reads its BM25
persist dir for collection listing, so that dir is pointed at ``tmp_path``.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.application.services import DocumentService, IngestionService, SystemService
from src.application.services.trace_store import TraceStore
from src.application.services.web_store import WebApiDB
from src.core.settings import Settings
from src.core.types import ChunkRecord
from src.ingestion.document_manager import DocumentManager
from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer, SparseEncoder
from src.web_api.app import create_app
from src.web_api.mappers import collection_uuid, document_uuid
from tests.unit.test_document_manager import (
    FakeChromaStore,
    FakeImageStorage,
    FakeIntegrity,
)


@pytest.fixture
def manager(tmp_path) -> DocumentManager:
    chroma = FakeChromaStore()
    chroma.upsert([
        ChunkRecord(id="c1", text="alpha", metadata={"source_path": "/a.pdf"}),
        ChunkRecord(id="c2", text="beta", metadata={"source_path": "/a.pdf"}),
    ])
    integrity = FakeIntegrity()
    integrity.mark_success("hash-a", "/a.pdf", file_size=100, last_modified=1.0)
    integrity.mark_success("hash-b", "/b.pdf", file_size=200, last_modified=2.0)
    images = FakeImageStorage()
    images.save("img1", b"x", doc_hash="hash-a")

    bm25_dir = tmp_path / "db" / "bm25"
    bm25_dir.mkdir(parents=True)
    (bm25_dir / "default.json").write_text(
        json.dumps({"n_docs": 0, "avgdl": 0.0, "k1": 1.5, "b": 0.75, "terms": {}}),
        encoding="utf-8",
    )
    return DocumentManager(
        chroma_store=chroma,
        bm25_indexer=BM25Indexer(
            persist_dir=str(bm25_dir), sparse_encoder=SparseEncoder(),
        ),
        bm25_index=BM25Index(),
        image_storage=images,
        file_integrity=integrity,
    )


class _NoOpPipeline:
    """Pipeline stub for tests that don't exercise the upload path."""

    def run(self, path: str, *, on_progress=None, trace=None, collection=None, source_path=None) -> None:
        return None


@pytest.fixture
def services(
    manager: DocumentManager, tmp_path,
) -> ApplicationServices:
    ingestion = IngestionService(
        _NoOpPipeline(), upload_dir=tmp_path / "uploads",
    )
    return ApplicationServices(
        query=object(),
        ingestion=ingestion,
        document=DocumentService(manager),
        system=SystemService(Settings()),
        trace=TraceStore(log_path=tmp_path / "traces.jsonl"),
        # These endpoints don't query / ingest; no EngineCache needed.
        engines=None,
        db=WebApiDB(tmp_path / "db" / "web_api.db"),
    )


@pytest.fixture
def client(services: ApplicationServices) -> TestClient:
    return TestClient(create_app(services=services))


# ---------------------------------------------------------------------------
# /system
# ---------------------------------------------------------------------------

class TestSystemInfo:
    def test_returns_metadata_and_providers(self, client: TestClient) -> None:
        resp = client.get("/api/v1/system/info")
        assert resp.status_code == 200
        body = resp.json()
        assert body["app_name"] == "skdy-rag-server"
        assert body["storage_backend"] == "chroma"
        assert set(body["providers"]) == {"llm", "embedding", "vision"}
        assert body["providers"]["llm"]["configured"] is True
        assert body["providers"]["llm"]["model"] == "gpt-4o"

    def test_never_leaks_secrets(self, client: TestClient) -> None:
        body = client.get("/api/v1/system/info").json()
        raw = json.dumps(body)
        for secret_key in ("api_key", "key", "secret", "token"):
            assert secret_key not in raw.lower()


class TestSystemHealth:
    def test_reports_dependencies(self, client: TestClient) -> None:
        """The fixture wires no probe collaborators, so the honest status
        is ``degraded`` (unverified) with the four known dependencies."""
        resp = client.get("/api/v1/system/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert {d["name"] for d in body["dependencies"]} == {
            "embedding", "chroma", "sqlite", "bm25",
        }


class TestOverviewMetrics:
    @staticmethod
    def _save_query(
        services: ApplicationServices,
        *,
        query_id: str,
        created_at: float,
        chunks: list[dict],
        latency_ms: float,
        degraded: bool = False,
    ) -> None:
        services.db.save_query_result(
            query_id=query_id,
            collection="default",
            query_text="test query",
            result_json=json.dumps({
                "chunks": chunks,
                "latency_ms": latency_ms,
                "degraded": degraded,
            }),
            document_ids=[],
            created_at=created_at,
        )

    def test_aggregates_quality_latency_and_ingestion(
        self, client: TestClient, services: ApplicationServices,
    ) -> None:
        import time

        now = time.time()
        self._save_query(
            services, query_id="current-good", created_at=now - 60,
            chunks=[{"text": "hit"}], latency_ms=500,
        )
        self._save_query(
            services, query_id="current-empty", created_at=now - 50,
            chunks=[], latency_ms=700,
        )
        self._save_query(
            services, query_id="current-degraded", created_at=now - 40,
            chunks=[{"text": "fallback"}], latency_ms=3000, degraded=True,
        )
        self._save_query(
            services, query_id="previous-good", created_at=now - 8 * 86_400,
            chunks=[{"text": "previous"}], latency_ms=1000,
        )

        for task_id, status in (("ingest-ok", "succeeded"), ("ingest-fail", "failed")):
            services.db.upsert_task({
                "task_id": task_id,
                "task_type": "ingestion",
                "status": status,
                "created_at": now - 30,
                "updated_at": now - 20,
                "finished_at": now - 20,
            })

        response = client.get("/api/v1/metrics/overview?range=7d")
        assert response.status_code == 200
        body = response.json()
        assert body["previous_query_count"] == 1
        assert body["query_count"] == 3
        assert body["metrics"]["effective_retrieval_rate"]["value"] == 33.3
        assert body["metrics"]["effective_retrieval_rate"]["previous"] == 100.0
        assert body["metrics"]["no_result_rate"]["value"] == 33.3
        assert body["metrics"]["p95_latency_ms"]["value"] == 3000.0
        assert body["metrics"]["ingestion_success_rate"]["value"] == 50.0
        assert body["failed_ingestions"] == 1
        assert len(body["trend"]) == 7
        assert body["traffic"]["request_count"] == 3
        assert body["traffic"]["success_rate"] == 100.0
        assert body["traffic"]["average_latency_ms"] == 1400.0
        assert body["traffic"]["token_usage"] is None
        assert body["retrieval_health"]["success_rate"] == 66.7
        assert body["retrieval_health"]["empty_retrieval_rate"] == 33.3
        assert body["retrieval_health"]["average_top_k"] == 0.7
        assert body["knowledge_base_health"]["document_count"] == 2
        assert body["knowledge_base_health"]["index_status"] == "ready"
        assert body["knowledge_bases"][0]["name"] == "default"
        assert body["status"] in {"attention", "critical"}
        assert body["attention"]

    def test_rejects_unknown_range(self, client: TestClient) -> None:
        assert client.get("/api/v1/metrics/overview?range=year").status_code == 422


# ---------------------------------------------------------------------------
# /collections
# ---------------------------------------------------------------------------

class TestListCollections:
    def test_lists_existing_collection(self, client: TestClient) -> None:
        resp = client.get("/api/v1/collections")
        assert resp.status_code == 200
        body = resp.json()
        names = [c["name"] for c in body["items"]]
        assert "default" in names
        assert body["page_info"]["has_more"] is False
        assert body["page_info"]["next_cursor"] is None

    def test_collection_summary_shape(self, client: TestClient) -> None:
        items = client.get("/api/v1/collections").json()["items"]
        default = next(c for c in items if c["name"] == "default")
        assert default["id"] == str(collection_uuid("default"))
        assert default["document_count"] == 2
        assert default["chunk_count"] == 2
        assert default["created_at"].endswith("Z")


class TestCreateCollection:
    def test_creates_and_returns_201(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/collections",
            json={"name": "reports", "description": "quarterly"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "reports"
        assert body["description"] == "quarterly"
        assert body["id"] == str(collection_uuid("reports"))

        listed = client.get("/api/v1/collections").json()["items"]
        reports = next(item for item in listed if item["name"] == "reports")
        assert reports["description"] == "quarterly"

        detail = client.get(f"/api/v1/collections/{body['id']}")
        assert detail.json()["description"] == "quarterly"

    def test_duplicate_returns_409(self, client: TestClient) -> None:
        first = client.post("/api/v1/collections", json={"name": "reports"})
        assert first.status_code == 201
        dup = client.post("/api/v1/collections", json={"name": "reports"})
        assert dup.status_code == 409
        assert dup.json()["error"]["code"] == "COLLECTION_ALREADY_EXISTS"


class TestGetCollection:
    def test_returns_detail(self, client: TestClient) -> None:
        cid = collection_uuid("default")
        resp = client.get(f"/api/v1/collections/{cid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "default"

    def test_unknown_returns_404(self, client: TestClient) -> None:
        import uuid
        resp = client.get(f"/api/v1/collections/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "COLLECTION_NOT_FOUND"


class TestUpdateCollection:
    def test_updates_and_clears_description(self, client: TestClient) -> None:
        cid = collection_uuid("default")
        updated = client.patch(
            f"/api/v1/collections/{cid}",
            json={"description": "Updated knowledge-base description"},
        )
        assert updated.status_code == 200
        assert updated.json()["description"] == "Updated knowledge-base description"

        listed = client.get("/api/v1/collections").json()["items"]
        default = next(item for item in listed if item["id"] == str(cid))
        assert default["description"] == "Updated knowledge-base description"

        cleared = client.patch(
            f"/api/v1/collections/{cid}", json={"description": None},
        )
        assert cleared.status_code == 200
        assert cleared.json()["description"] is None

    def test_unknown_returns_404(self, client: TestClient) -> None:
        import uuid
        resp = client.patch(
            f"/api/v1/collections/{uuid.uuid4()}", json={"description": "x"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "COLLECTION_NOT_FOUND"


class TestCollectionDocuments:
    def test_lists_documents(self, client: TestClient) -> None:
        cid = collection_uuid("default")
        resp = client.get(f"/api/v1/collections/{cid}/documents")
        assert resp.status_code == 200
        body = resp.json()
        filenames = [d["filename"] for d in body["items"]]
        assert set(filenames) == {"a.pdf", "b.pdf"}
        assert body["items"][0]["status"] == "ready"

    def test_unknown_collection_returns_404(self, client: TestClient) -> None:
        import uuid
        resp = client.get(f"/api/v1/collections/{uuid.uuid4()}/documents")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /documents
# ---------------------------------------------------------------------------

class TestGetDocument:
    def test_returns_detail_with_hash(self, client: TestClient) -> None:
        did = document_uuid("default", "/a.pdf")
        resp = client.get(f"/api/v1/documents/{did}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == "a.pdf"
        assert body["file_hash"] == "hash-a"
        assert body["chunk_count"] == 2
        assert body["last_task_id"] is None

    def test_unknown_returns_404(self, client: TestClient) -> None:
        import uuid
        resp = client.get(f"/api/v1/documents/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

    def test_malformed_id_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/documents/not-a-uuid")
        assert resp.status_code == 404


class TestDeleteDocument:
    def test_deletes_then_404(self, client: TestClient) -> None:
        did = document_uuid("default", "/a.pdf")
        resp = client.delete(f"/api/v1/documents/{did}")
        assert resp.status_code == 204
        assert client.get(f"/api/v1/documents/{did}").status_code == 404


class TestDeleteCollection:
    def test_cascades_and_removes_marker(self, client: TestClient) -> None:
        cid = collection_uuid("default")
        resp = client.delete(f"/api/v1/collections/{cid}")
        assert resp.status_code == 204
        # Collection gone from listing; its documents gone too.
        names = [c["name"] for c in client.get("/api/v1/collections").json()["items"]]
        assert "default" not in names
        assert client.get(
            f"/api/v1/documents/{document_uuid('default', '/a.pdf')}",
        ).status_code == 404
