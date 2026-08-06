"""
M2 batch 3 — real ``POST /collections/{id}/queries``.

Five cases: happy path, empty collection (degraded), unknown collection
404, invalid query 422, embedding provider failure 502.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from src.web_api.app import create_app
from src.web_api.mappers import collection_uuid, document_uuid
from tests.integration._batch3_helpers import (
    FakeHybridSearch,
    build_batch3_services,
    retrieval_result,
)
from src.libs.embedding import EmbeddingError


@pytest.fixture
def client(tmp_path) -> TestClient:
    services = build_batch3_services(
        tmp_path,
        hybrid=FakeHybridSearch([
            retrieval_result(
                "c1", "/a.pdf", score=0.5, page=3,
                images=[{"id": "img-1"}],
            ),
        ]),
    )
    return TestClient(create_app(services=services))


class TestQueryHappyPath:
    def test_returns_citations_and_diagnostics(self, client: TestClient) -> None:
        cid = collection_uuid("default")
        resp = client.post(
            f"/api/v1/collections/{cid}/queries",
            json={"query": "年假怎么规定?", "top_k": 5, "mode": "hybrid"},
        )
        assert resp.status_code == 200
        body = resp.json()

        # Citation mapping: index, chunk id, stable document id, name.
        cite = body["citations"][0]
        assert cite["index"] == 1
        assert cite["chunk_id"] == "c1"
        assert cite["document_id"] == str(document_uuid("default", "/a.pdf"))
        assert cite["document_name"] == "a.pdf"
        assert cite["page"] == 3
        assert cite["text"] == "chunk text here"
        # Hybrid path → the fused score lands in ``scores.fusion``.
        assert cite["scores"]["fusion"] == 0.5
        assert cite["scores"]["dense"] is None
        # Images map to relative URLs, never filesystem paths.
        assert cite["images"] == [
            {"id": "img-1", "url": "/api/v1/images/img-1", "caption": None},
        ]

        diag = body["diagnostics"]
        assert diag["duration_ms"] >= 0.0
        assert diag["trace_id"] == body["query_id"]  # query_id == trace_id
        assert diag["dense_count"] == 1
        assert diag["sparse_count"] == 1
        assert diag["fused_count"] == 1
        assert diag["reranked_count"] is None  # rerank not wired (v0.2)
        assert diag["degraded"] is False

    def test_answer_is_null_in_v02(self, client: TestClient) -> None:
        cid = collection_uuid("default")
        body = client.post(
            f"/api/v1/collections/{cid}/queries", json={"query": "hi"},
        ).json()
        assert body["answer"] is None


class TestEmptyCollection:
    def test_returns_degraded_with_no_results(self, tmp_path) -> None:
        services = build_batch3_services(
            tmp_path, hybrid=FakeHybridSearch([]),
        )
        client = TestClient(create_app(services=services))
        resp = client.post(
            f"/api/v1/collections/{collection_uuid('default')}/queries",
            json={"query": "nothing here"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["citations"] == []
        assert body["diagnostics"]["degraded"] is True
        assert "no chunks matched the query" in body["diagnostics"]["degraded_reasons"]


class TestQueryErrors:
    def test_unknown_collection_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            f"/api/v1/collections/{uuid.uuid4()}/queries",
            json={"query": "hi"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "COLLECTION_NOT_FOUND"

    def test_invalid_query_returns_422(self, client: TestClient) -> None:
        cid = collection_uuid("default")
        # Empty query violates min_length=1.
        resp = client.post(
            f"/api/v1/collections/{cid}/queries", json={"query": ""},
        )
        assert resp.status_code == 422
        # top_k out of the [1, 50] range is also a validation error.
        resp = client.post(
            f"/api/v1/collections/{cid}/queries",
            json={"query": "hi", "top_k": 0},
        )
        assert resp.status_code == 422
        # Unknown mode → Literal validation fails.
        resp = client.post(
            f"/api/v1/collections/{cid}/queries",
            json={"query": "hi", "mode": "vector"},
        )
        assert resp.status_code == 422

    def test_embedding_failure_returns_502(self, tmp_path) -> None:
        services = build_batch3_services(
            tmp_path,
            hybrid=FakeHybridSearch(
                dense_raises=EmbeddingError("upstream down"),
            ),
        )
        client = TestClient(create_app(services=services))
        resp = client.post(
            f"/api/v1/collections/{collection_uuid('default')}/queries",
            json={"query": "hi", "mode": "dense"},
        )
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "UPSTREAM_ERROR"
