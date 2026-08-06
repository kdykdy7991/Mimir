"""
M3 batch 2 — async queries, SQLite persistence, ``DocumentDetail.last_query_id``.

Exercises the additive contract:

- ``POST /collections/{id}/queries/async`` → 202 ``AsyncQueryAccepted``
- ``GET /queries/{query_id}/result`` → ``AsyncQueryResult`` (poll)
- task status survives a process restart (SQLite-backed ``TaskTracker``)
- ``DocumentDetail.last_query_id`` links a document back to the last
  query that cited it

The existing synchronous ``POST /queries`` is untouched.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.application.services.web_store import WebApiDB
from src.core.types import ChunkRecord
from src.ingestion.document_manager import DocumentManager
from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer, SparseEncoder
from src.web_api.app import create_app
from src.web_api.mappers import document_uuid
from tests.integration._batch3_helpers import (
    FakeChromaStore,
    FakeImageStorage,
    FakeIntegrity,
    build_batch3_services,
    retrieval_result,
)
from src.libs.embedding import EmbeddingError
from src.web_api.schemas.queries import (
    AsyncQueryAccepted,
    AsyncQueryResult,
    QueryRequest,
    QueryResponse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path) -> WebApiDB:
    return WebApiDB(tmp_path / "db" / "web_api.db")


def _manager_with_document(tmp_path, *, source_path: str = "/a.pdf"):
    """A DocumentManager that already knows ``source_path`` (chunk + integrity)."""
    chroma = FakeChromaStore()
    chroma.upsert([
        ChunkRecord(id="c1", text="alpha", metadata={"source_path": source_path}),
    ])
    integrity = FakeIntegrity()
    integrity.mark_success("hash-a", source_path, file_size=100)
    images = FakeImageStorage()
    images.save("img1", b"x", doc_hash="hash-a")

    bm25_dir = tmp_path / "db" / "bm25"
    bm25_dir.mkdir(parents=True, exist_ok=True)
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


@pytest.fixture
def services(db: WebApiDB, tmp_path) -> ApplicationServices:
    return build_batch3_services(
        tmp_path,
        db=db,
        manager=_manager_with_document(tmp_path),
        hybrid=_citing_hybrid(),
    )


@pytest.fixture
def client(services: ApplicationServices) -> TestClient:
    return TestClient(create_app(services=services))


def _citing_hybrid(results=None):
    """A FakeHybridSearch whose chunks cite ``/a.pdf``."""
    from tests.integration._batch3_helpers import FakeHybridSearch

    return FakeHybridSearch(
        results or [retrieval_result("c1", "/a.pdf", page=1)],
    )


def _poll_result(client: TestClient, query_id: str, timeout: float = 3.0) -> AsyncQueryResult:
    """Poll ``GET /queries/{id}/result`` until a terminal status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/queries/{query_id}/result")
        assert resp.status_code == 200, resp.text
        state = AsyncQueryResult.model_validate(resp.json())
        if state.status in ("succeeded", "failed"):
            return state
        time.sleep(0.02)
    raise AssertionError(f"async query {query_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# Async query
# ---------------------------------------------------------------------------

class TestAsyncQuery:
    def test_submit_returns_accepted_with_matching_ids(
        self, client: TestClient,
    ) -> None:
        resp = client.post(
            "/api/v1/collections/{}/queries/async".format(
                _collection_id(client),
            ),
            json={"query": "vector search", "top_k": 5},
        )
        assert resp.status_code == 202, resp.text
        accepted = AsyncQueryAccepted.model_validate(resp.json())
        assert accepted.status == "accepted"
        assert accepted.query_id == accepted.task_id

    def test_poll_result_eventually_succeeds(
        self, client: TestClient,
    ) -> None:
        cid = _collection_id(client)
        accepted = AsyncQueryAccepted.model_validate(client.post(
            f"/api/v1/collections/{cid}/queries/async",
            json={"query": "vector search"},
        ).json())
        state = _poll_result(client, str(accepted.query_id))
        assert state.status == "succeeded"
        assert state.result is not None
        assert state.result.citations, "expected at least one citation"
        assert state.error is None
        # trace is reachable with the same id (query_id == trace_id)
        trace = client.get(f"/api/v1/queries/{accepted.query_id}/trace")
        assert trace.status_code == 200, trace.text

    def test_unknown_query_returns_404(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/queries/00000000-0000-0000-0000-000000000000/result",
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "QUERY_NOT_FOUND"

    def test_failed_query_reports_upstream_error(
        self, db: WebApiDB, tmp_path,
    ) -> None:
        from tests.integration._batch3_helpers import FakeHybridSearch

        failing = FakeHybridSearch(
            [retrieval_result("c1", "/a.pdf")], dense_raises=EmbeddingError(),
        )
        services = build_batch3_services(
            tmp_path, db=db, manager=_manager_with_document(tmp_path),
            hybrid=failing,
        )
        client = TestClient(create_app(services=services))
        cid = _collection_id(client)
        accepted = AsyncQueryAccepted.model_validate(client.post(
            f"/api/v1/collections/{cid}/queries/async",
            json={"query": "x", "mode": "dense"},
        ).json())
        state = _poll_result(client, str(accepted.query_id))
        assert state.status == "failed"
        assert state.result is None
        assert state.error is not None
        assert state.error.code == "UPSTREAM_ERROR"

    def test_query_task_not_served_by_tasks_endpoint(
        self, client: TestClient,
    ) -> None:
        cid = _collection_id(client)
        accepted = AsyncQueryAccepted.model_validate(client.post(
            f"/api/v1/collections/{cid}/queries/async",
            json={"query": "vector search"},
        ).json())
        # The query task id is a task, but /tasks/{id} is ingestion-only.
        resp = client.get(f"/api/v1/tasks/{accepted.task_id}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"


# ---------------------------------------------------------------------------
# last_query_id
# ---------------------------------------------------------------------------

class TestLastQueryId:
    def test_sync_query_populates_last_query_id(
        self, client: TestClient,
    ) -> None:
        cid = _collection_id(client)
        # Run a sync query that cites /a.pdf.
        resp = client.post(
            f"/api/v1/collections/{cid}/queries",
            json={"query": "vector search", "top_k": 5},
        )
        assert resp.status_code == 200, resp.text
        response = QueryResponse.model_validate(resp.json())
        assert response.query_id is not None

        # The cited document now reports that query as its last one.
        doc_id = document_uuid("default", "/a.pdf")
        detail = client.get(f"/api/v1/documents/{doc_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["last_query_id"] == str(response.query_id)

    def test_uncited_document_has_no_last_query(self, db: WebApiDB, tmp_path) -> None:
        # No query ever ran → last_query_id stays null.
        services = build_batch3_services(
            tmp_path, db=db, manager=_manager_with_document(tmp_path),
        )
        client = TestClient(create_app(services=services))
        doc_id = document_uuid("default", "/a.pdf")
        detail = client.get(f"/api/v1/documents/{doc_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["last_query_id"] is None


# ---------------------------------------------------------------------------
# SQLite persistence across restart
# ---------------------------------------------------------------------------

class TestTaskPersistence:
    def test_ingestion_task_survives_restart(
        self, db: WebApiDB, tmp_path,
    ) -> None:
        from tests.integration._batch3_helpers import FakePipeline

        # Boot 1: run an ingestion (FakePipeline succeeds immediately).
        services1 = build_batch3_services(
            tmp_path, db=db, pipeline=FakePipeline(),
        )
        client1 = TestClient(create_app(services=services1))
        cid = _collection_id(client1)
        upload = client1.post(
            f"/api/v1/collections/{cid}/documents",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert upload.status_code == 202, upload.text
        task_id = upload.json()["task_id"]

        # "Restart": a brand-new services stack over the same db.
        services2 = build_batch3_services(tmp_path, db=db)
        client2 = TestClient(create_app(services=services2))
        status = client2.get(f"/api/v1/tasks/{task_id}")
        assert status.status_code == 200, status.text
        assert status.json()["id"] == task_id

    def test_async_query_result_survives_restart(
        self, db: WebApiDB, tmp_path,
    ) -> None:
        services1 = build_batch3_services(
            tmp_path, db=db, manager=_manager_with_document(tmp_path),
            hybrid=_citing_hybrid(),
        )
        client1 = TestClient(create_app(services=services1))
        cid = _collection_id(client1)
        accepted = AsyncQueryAccepted.model_validate(client1.post(
            f"/api/v1/collections/{cid}/queries/async",
            json={"query": "vector search"},
        ).json())
        # Wait for completion on boot 1.
        _poll_result(client1, str(accepted.query_id))

        # "Restart": new services stack on the same db → result still readable.
        services2 = build_batch3_services(
            tmp_path, db=db, manager=_manager_with_document(tmp_path),
            hybrid=_citing_hybrid(),
        )
        client2 = TestClient(create_app(services=services2))
        state = _poll_result(client2, str(accepted.query_id))
        assert state.status == "succeeded"
        assert state.result is not None
        assert state.result.citations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collection_id(client: TestClient) -> str:
    """Ensure the ``default`` collection exists and return its id."""
    items = client.get("/api/v1/collections").json()["items"]
    default = next((c for c in items if c["name"] == "default"), None)
    if default is not None:
        return default["id"]
    resp = client.post(
        "/api/v1/collections", json={"name": "default"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


__all__ = []
