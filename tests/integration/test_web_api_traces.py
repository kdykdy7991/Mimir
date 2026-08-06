"""
M2 batch 3 — real ``GET /queries/{id}/trace`` + ``GET /ingestions/{id}/trace``.

Four cases: query trace 200, ingestion trace 200, unknown id 404, and
"task exists but trace lost" → 200 with empty stages.
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.web_api.app import create_app
from src.web_api.mappers import collection_uuid, document_uuid
from tests.integration._batch3_helpers import (
    FakeHybridSearch,
    FakePipeline,
    build_batch3_services,
    retrieval_result,
)


@pytest.fixture
def client(tmp_path) -> TestClient:
    services = build_batch3_services(
        tmp_path,
        hybrid=FakeHybridSearch([retrieval_result("c1", "/a.pdf")]),
    )
    return TestClient(create_app(services=services))


class TestQueryTrace:
    def test_returns_stages_for_a_recorded_query(self, client: TestClient) -> None:
        cid = collection_uuid("default")
        resp = client.post(
            f"/api/v1/collections/{cid}/queries", json={"query": "hi"},
        )
        query_id = resp.json()["query_id"]

        trace = client.get(f"/api/v1/queries/{query_id}/trace")
        assert trace.status_code == 200
        body = trace.json()
        assert body["id"] == query_id
        assert body["trace_type"] == "query"
        assert body["stages"], "expected orchestrator-level stages"
        names = {s["name"] for s in body["stages"]}
        assert {"query_processing", "dense_retrieval", "sparse_retrieval"} <= names
        for stage in body["stages"]:
            assert stage["duration_ms"] >= 0.0
            assert stage["started_at"].endswith("Z")

    def test_unknown_query_id_returns_404(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/queries/{uuid.uuid4()}/trace")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "QUERY_NOT_FOUND"


class TestIngestionTrace:
    def _run_upload(self, services: ApplicationServices) -> uuid.UUID:
        task = services.ingestion.upload(
            bytes_payload=b"%PDF-1.4 fake",
            filename="a.pdf",
            collection="default",
            collection_id=collection_uuid("default"),
            document_id=document_uuid("default", "/uploads/a.pdf"),
            source_path=None,
        )
        deadline = time.time() + 3.0
        while time.time() < deadline:
            rec = services.ingestion.get_task(task.id)
            if rec is not None and rec.status in ("succeeded", "failed"):
                return task.id
            time.sleep(0.01)
        raise AssertionError("ingestion task did not reach terminal state")

    def test_returns_stages_for_an_ingestion(self, tmp_path) -> None:
        services = build_batch3_services(tmp_path)
        client = TestClient(create_app(services=services))
        task_id = self._run_upload(services)

        resp = client.get(f"/api/v1/ingestions/{task_id}/trace")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(task_id)
        assert body["trace_type"] == "ingestion"
        names = {s["name"] for s in body["stages"]}
        assert {"load", "split", "embed", "upsert"} <= names

    def test_task_without_trace_returns_200_empty(
        self, tmp_path,
    ) -> None:
        # Ingestion wired WITHOUT a trace store → the task is tracked
        # but no trace is ever recorded (the "record exists, timeline
        # lost" case from the v0.2 design).
        services = build_batch3_services(
            tmp_path, ingestion_trace_store=None,
        )
        client = TestClient(create_app(services=services))
        task_id = self._run_upload(services)

        resp = client.get(f"/api/v1/ingestions/{task_id}/trace")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stages"] == []
        assert body["trace_type"] == "ingestion"
        assert body["error"] is None

    def test_unknown_ingestion_id_returns_404(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/ingestions/{uuid.uuid4()}/trace")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "INGESTION_NOT_FOUND"
