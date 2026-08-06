"""
``/system/health`` + ``/system/info`` real probe semantics (M3 收尾).

The fixtures wire real probe collaborators into ``SystemService`` so the
endpoint reflects actual dependency health: healthy stack → ``ok``,
failing embedding → ``down``, and ``/system/info``'s provider ``ready``
tracks the probe result.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.application.services import SystemService
from src.core.settings import Settings
from src.libs.embedding import EmbeddingError
from src.web_api.app import create_app


class _OkEmbedding:
    def embed_single(self, text: str) -> list[float]:
        return [0.1, 0.2]


class _FailEmbedding:
    def embed_single(self, text: str) -> list[float]:
        raise EmbeddingError("connection refused")


class _OkStore:
    def heartbeat(self) -> dict:
        return {"nanosecond heartbeat": 1}


class _OkDb:
    def ping(self) -> None:
        return None


def _client(*, embedding=None, vector_store=None, db=None, data_dir=None) -> TestClient:
    services = ApplicationServices(
        query=object(),
        ingestion=object(),
        document=object(),
        system=SystemService(
            Settings(),
            embedding=embedding,
            vector_store=vector_store,
            db=db,
            data_dir=data_dir,
        ),
        trace=object(),
        engines=None,
    )
    return TestClient(create_app(services=services))


def test_healthy_stack_reports_ok(tmp_path) -> None:
    client = _client(
        embedding=_OkEmbedding(), vector_store=_OkStore(),
        db=_OkDb(), data_dir=str(tmp_path),
    )
    resp = client.get("/api/v1/system/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert {d["status"] for d in body["dependencies"]} == {"ok"}
    names = {d["name"] for d in body["dependencies"]}
    assert names == {"embedding", "chroma", "sqlite", "bm25"}


def test_failed_embedding_reports_down(tmp_path) -> None:
    client = _client(
        embedding=_FailEmbedding(), vector_store=_OkStore(),
        db=_OkDb(), data_dir=str(tmp_path),
    )
    resp = client.get("/api/v1/system/health")
    body = resp.json()
    assert body["status"] == "down"
    embedding = next(d for d in body["dependencies"] if d["name"] == "embedding")
    assert embedding["status"] == "down"


def test_info_ready_tracks_embedding_probe(tmp_path) -> None:
    healthy = _client(
        embedding=_OkEmbedding(), vector_store=_OkStore(),
        db=_OkDb(), data_dir=str(tmp_path),
    )
    info = healthy.get("/api/v1/system/info").json()
    assert info["providers"]["embedding"]["ready"] is True

    broken = _client(
        embedding=_FailEmbedding(), vector_store=_OkStore(),
        db=_OkDb(), data_dir=str(tmp_path),
    )
    info = broken.get("/api/v1/system/info").json()
    assert info["providers"]["embedding"]["ready"] is False


__all__ = []
