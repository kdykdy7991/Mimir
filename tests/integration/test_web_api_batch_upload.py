"""
M5 — batch ``POST /collections/{id}/documents`` (``files[]``).

Mirrors ``test_web_api_upload.py``: a fake pipeline + background worker,
so we only verify the **boundary** between the router, the batch service
and the task tracker. Covers:

- ``files=[pdf, md]`` → 202 + ``batch_id`` + one accepted result each;
- mixed batch with a validation-rejected file → others still accepted
  (single-file failure never blocks the batch);
- batch-level limits (file count / total bytes) → 413;
- mode dispatch: ``file``+``files`` and neither → 400;
- duplicate dedup → ``skipped`` (via worker) and pre-check skip;
- single-file ``file=`` upload still returns ``DocumentUploadResponse``.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.application.services import (
    DocumentService,
    IngestionService,
    SystemService,
    TaskRecord,
)
from src.application.services.trace_store import TraceStore
from src.core.settings import Settings
from src.ingestion.document_manager import DocumentManager
from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer, SparseEncoder
from src.web_api.app import create_app
from src.web_api.mappers import collection_uuid
from tests.unit.test_document_manager import (
    FakeChromaStore,
    FakeImageStorage,
    FakeIntegrity,
)


class _FakeResult:
    skipped = False
    n_chunks = 3
    n_records_upserted = 3
    bm25_n_docs = 3
    n_images_saved = 0
    errors: list[str] = []


class _AlwaysSkipIntegrity:
    """``should_skip`` always True — simulates an already-ingested file."""

    def should_skip(self, file_hash: str, *, collection: str = "default") -> bool:
        return True


class _FakePipeline:
    """Synthetic pipeline: success / fail / skip / pre-check-skip variants."""

    def __init__(self, behaviour: str = "success", *, integrity: Any = None) -> None:
        self.behaviour = behaviour
        self.integrity = integrity
        self.calls: list[dict[str, Any]] = []

    @property
    def file_integrity(self):
        return self.integrity

    def run(self, path: str, *, on_progress=None, trace=None, collection=None, source_path=None) -> Any:
        self.calls.append({"path": path, "trace": trace, "collection": collection})
        if on_progress is not None:
            on_progress("load", 1, 1)
            on_progress("split", 1, 1)
            on_progress("embed", 1, 1)
            on_progress("upsert", 1, 1)
        if self.behaviour == "fail":
            from src.ingestion.pipeline import PipelineStageError
            raise PipelineStageError("embed", "embedding provider timed out")
        result = _FakeResult()
        result.skipped = self.behaviour == "skip"
        return result


def _build_services(tmp_path, pipeline: _FakePipeline) -> ApplicationServices:
    """Assemble application services with a fake pipeline over tmp storage."""
    bm25_dir = tmp_path / "db" / "bm25"
    bm25_dir.mkdir(parents=True)
    (bm25_dir / "default.json").write_text(
        '{"n_docs": 0, "avgdl": 0.0, "k1": 1.5, "b": 0.75, "terms": {}}',
        encoding="utf-8",
    )

    ingestion = IngestionService(pipeline, upload_dir=tmp_path / "uploads")
    (tmp_path / "uploads" / "default").mkdir(parents=True, exist_ok=True)

    document = DocumentService(
        DocumentManager(
            chroma_store=FakeChromaStore(),
            bm25_indexer=BM25Indexer(
                persist_dir=str(bm25_dir),
                sparse_encoder=SparseEncoder(),
            ),
            bm25_index=BM25Index(),
            image_storage=FakeImageStorage(),
            file_integrity=FakeIntegrity(),
        ),
    )
    return ApplicationServices(
        query=object(),
        ingestion=ingestion,
        document=document,
        system=SystemService(Settings()),
        trace=TraceStore(log_path=tmp_path / "traces.jsonl"),
        engines=None,
    )


def _wait_for_terminal(
    ingestion: IngestionService, task_id, timeout: float = 3.0,
) -> TaskRecord:
    from uuid import UUID as _UUID
    if isinstance(task_id, str):
        task_id = _UUID(task_id)
    deadline = time.monotonic() + timeout
    last_seen = None
    while time.monotonic() < deadline:
        record = ingestion.get_task(task_id)
        if record is not None:
            last_seen = record.status
            if record.status in {"succeeded", "failed", "skipped"}:
                return record
        time.sleep(0.01)
    pytest.fail(
        f"task {task_id} did not reach a terminal state within {timeout}s; "
        f"last seen status={last_seen}",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestBatchUpload:
    def test_batch_pdf_and_markdown_returns_202(self, tmp_path) -> None:
        services = _build_services(tmp_path, _FakePipeline())
        client = TestClient(create_app(services=services))
        cid = collection_uuid("default")

        resp = client.post(
            f"/api/v1/collections/{cid}/documents",
            files=[
                ("files", ("a.pdf", b"%PDF-1.4", "application/pdf")),
                ("files", ("b.md", b"# Title\n\nbody", "text/markdown")),
            ],
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["batch_id"]
        assert body["collection_id"] == str(cid)
        assert body["total"] == 2
        assert body["accepted"] == 2 and body["skipped"] == 0 and body["rejected"] == 0
        assert len(body["files"]) == 2
        for f in body["files"]:
            assert f["status"] == "accepted"
            assert f["task_id"] is not None
            assert f["document_id"] is not None
            assert f["error"] is None
        # The tasks are real: poll them to a terminal state.
        for f in body["files"]:
            record = _wait_for_terminal(services.ingestion, f["task_id"])
            assert record.status == "succeeded"

    def test_rejected_file_does_not_block_others(self, tmp_path) -> None:
        services = _build_services(tmp_path, _FakePipeline())
        client = TestClient(create_app(services=services))
        cid = collection_uuid("default")

        resp = client.post(
            f"/api/v1/collections/{cid}/documents",
            files=[
                ("files", ("a.pdf", b"%PDF-1.4", "application/pdf")),
                ("files", ("bad.txt", b"nope", "text/plain")),
                ("files", ("b.md", b"# T", "text/markdown")),
            ],
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["total"] == 3
        assert body["accepted"] == 2 and body["rejected"] == 1
        rejected = next(f for f in body["files"] if f["status"] == "rejected")
        assert rejected["filename"] == "bad.txt"
        assert rejected["task_id"] is None
        assert rejected["document_id"] is None
        assert rejected["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
        # The two valid files still ingested.
        for f in body["files"]:
            if f["status"] == "accepted":
                assert _wait_for_terminal(services.ingestion, f["task_id"]).status == "succeeded"

    def test_unknown_collection_returns_404(self, tmp_path) -> None:
        services = _build_services(tmp_path, _FakePipeline())
        client = TestClient(create_app(services=services))
        import uuid
        resp = client.post(
            f"/api/v1/collections/{uuid.uuid4()}/documents",
            files=[("files", ("a.pdf", b"%PDF", "application/pdf"))],
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "COLLECTION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Dedup → skipped
# ---------------------------------------------------------------------------

class TestBatchSkipped:
    def test_worker_marks_duplicate_task_skipped(self, tmp_path) -> None:
        # Pipeline with no file_integrity -> pre-check skipped, the worker
        # runs and reports skipped=True, so the task ends "skipped".
        services = _build_services(tmp_path, _FakePipeline(behaviour="skip"))
        client = TestClient(create_app(services=services))
        cid = collection_uuid("default")

        resp = client.post(
            f"/api/v1/collections/{cid}/documents",
            files=[("files", ("dup.md", b"# x", "text/markdown"))],
        )
        assert resp.status_code == 202
        task_id = resp.json()["files"][0]["task_id"]
        record = _wait_for_terminal(services.ingestion, task_id)
        assert record.status == "skipped"
        assert record.finished_at is not None
        assert record.progress is None

    def test_precheck_marks_skipped_in_response(self, tmp_path) -> None:
        # Pipeline exposes file_integrity whose should_skip -> True: the
        # batch response itself reports "skipped", no task created.
        services = _build_services(
            tmp_path, _FakePipeline(integrity=_AlwaysSkipIntegrity()),
        )
        client = TestClient(create_app(services=services))
        cid = collection_uuid("default")

        resp = client.post(
            f"/api/v1/collections/{cid}/documents",
            files=[("files", ("dup.pdf", b"%PDF", "application/pdf"))],
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["skipped"] == 1 and body["accepted"] == 0
        f = body["files"][0]
        assert f["status"] == "skipped"
        assert f["task_id"] is None
        assert f["document_id"] is not None


# ---------------------------------------------------------------------------
# Batch-level limits + mode dispatch
# ---------------------------------------------------------------------------

class TestBatchLimits:
    def test_too_many_files_returns_413(self, tmp_path) -> None:
        services = _build_services(tmp_path, _FakePipeline())
        client = TestClient(create_app(services=services))
        cid = collection_uuid("default")

        many = [("files", (f"f{i}.pdf", b"%PDF", "application/pdf")) for i in range(51)]
        resp = client.post(f"/api/v1/collections/{cid}/documents", files=many)
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    def test_batch_total_bytes_returns_413(self, tmp_path, monkeypatch) -> None:
        import src.web_api.routers.collections as collections_router
        from src.web_api.settings import WebAPISettings
        monkeypatch.setattr(
            collections_router, "SETTINGS",
            WebAPISettings(upload_max_batch_bytes=10),
        )
        services = _build_services(tmp_path, _FakePipeline())
        client = TestClient(create_app(services=services))
        cid = collection_uuid("default")

        resp = client.post(
            f"/api/v1/collections/{cid}/documents",
            files=[("files", ("a.pdf", b"%PDF-1.4 fake", "application/pdf"))],
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    def test_file_and_files_together_returns_400(self, tmp_path) -> None:
        services = _build_services(tmp_path, _FakePipeline())
        client = TestClient(create_app(services=services))
        cid = collection_uuid("default")

        resp = client.post(
            f"/api/v1/collections/{cid}/documents",
            files={
                "file": ("a.pdf", b"%PDF", "application/pdf"),
                "files": ("b.md", b"# x", "text/markdown"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "BAD_REQUEST"

    def test_no_file_returns_400(self, tmp_path) -> None:
        services = _build_services(tmp_path, _FakePipeline())
        client = TestClient(create_app(services=services))
        cid = collection_uuid("default")

        resp = client.post(f"/api/v1/collections/{cid}/documents")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "BAD_REQUEST"


# ---------------------------------------------------------------------------
# Single-file compatibility
# ---------------------------------------------------------------------------

class TestSingleFileCompat:
    def test_single_file_still_returns_document_upload_response(self, tmp_path) -> None:
        services = _build_services(tmp_path, _FakePipeline())
        client = TestClient(create_app(services=services))
        cid = collection_uuid("default")

        resp = client.post(
            f"/api/v1/collections/{cid}/documents",
            files={"file": ("solo.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "document" in body and "task_id" in body
        assert "batch_id" not in body
        assert body["document"]["filename"] == "solo.pdf"

    def test_single_file_unsupported_extension_returns_415(self, tmp_path) -> None:
        services = _build_services(tmp_path, _FakePipeline())
        client = TestClient(create_app(services=services))
        cid = collection_uuid("default")

        resp = client.post(
            f"/api/v1/collections/{cid}/documents",
            files={"file": ("data.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
