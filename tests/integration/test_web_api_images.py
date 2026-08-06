"""
M2 batch 3 — real ``GET /images/{image_id}``.

Three cases: 200 with matching bytes + MIME, unknown id 404, and
path-traversal / charset defence → 404.

These use a *real* ``ImageStorage`` (SQLite + disk) so the endpoint
serves actual bytes — the same code path production runs.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.ingestion.storage.image_storage import ImageStorage
from src.web_api.app import create_app
from tests.integration._batch3_helpers import build_batch3_services

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fakepayload" + b"\x00\xff"


@pytest.fixture
def client(tmp_path) -> TestClient:
    images = ImageStorage(
        db_path=str(tmp_path / "db" / "image_index.db"),
        base_dir=str(tmp_path / "images"),
    )
    services = build_batch3_services(tmp_path, image_storage=images)
    # Seed one indexed image the way the pipeline would.
    services.document.image_storage.save("img-1", PNG_BYTES, "png")
    return TestClient(create_app(services=services))


class TestGetImage:
    def test_returns_bytes_with_mime_and_cache_header(
        self, client: TestClient,
    ) -> None:
        resp = client.get("/api/v1/images/img-1")
        assert resp.status_code == 200
        assert resp.content == PNG_BYTES
        assert resp.headers["content-type"] == "image/png"
        assert resp.headers["cache-control"] == "public, max-age=86400"

    def test_unknown_image_id_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/images/nope")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "IMAGE_NOT_FOUND"

    def test_path_traversal_ids_are_rejected(self, client: TestClient) -> None:
        # Encoded slashes / dots are outside the ``[A-Za-z0-9_-]`` charset
        # and must never probe storage.
        for bad in (
            "..%2fetc%2fpasswd",
            "x.y",
            "img!1",
            "%2e%2e",
        ):
            resp = client.get(f"/api/v1/images/{bad}")
            assert resp.status_code == 404, f"{bad!r} should be rejected"
