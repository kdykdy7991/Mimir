"""B3.3 + B3.4 — HTTP-level retry and cooperative cancel of ingestion tasks.

Covers: retry allowed/rejected states + idempotency + missing source file +
parent/child trace relationship; cancel of a running task (between stages),
queued/pending cancel, repeated cancel, cancel-after-complete, terminal trace.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.application.services.task_types import TaskError
from src.web_api.app import create_app
from src.web_api.mappers import collection_uuid
from tests.integration._batch3_helpers import FakePipeline, build_batch3_services


class _GatedPipeline:
    """Pipeline that blocks after ``load`` until the test releases a gate.

    Used to hold a task in the ``running`` stage so a cancel can land between
    stages (before the next ``on_progress`` boundary).
    """

    def __init__(self, gate: threading.Event) -> None:
        self.gate = gate
        self.runs: list[dict] = []

    def run(self, path, *, on_progress=None, trace=None, collection=None, source_path=None):
        self.runs.append({"path": str(path)})
        if trace is not None:
            trace.record_stage("load", method="Fake", elapsed_ms=1.0)
        if on_progress is not None:
            on_progress("load", 1, 1)
        if not self.gate.wait(5.0):
            raise TimeoutError("gate not released")
        if trace is not None:
            trace.record_stage("embed", method="Fake", elapsed_ms=1.0)
        if on_progress is not None:
            on_progress("embed", 1, 1)  # raises TaskCancelledError if cancelled
        return FakeResult()


class _FakeResult:
    skipped = False


def _cid() -> str:
    return str(collection_uuid("default"))


def _poll(client: TestClient, path: str, wanted, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(path)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in wanted:
            return body
        time.sleep(0.02)
    raise AssertionError(f"{path} never reached {wanted}")


# ---------------------------------------------------------------------------
# B3.4 — cancel
# ---------------------------------------------------------------------------

class TestCancel:
    def test_between_stage_cancel_marks_cancelled_and_terminal_trace(self, tmp_path):
        gate = threading.Event()
        services = build_batch3_services(
            tmp_path, pipeline=_GatedPipeline(gate),
        )
        client = TestClient(create_app(services=services))

        resp = client.post(
            f"/api/v1/collections/{_cid()}/documents",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code == 202, resp.text
        task_id = resp.json()["task_id"]

        _poll(client, f"/api/v1/tasks/{task_id}", {"running"})

        cancel = client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert cancel.status_code == 202, cancel.text

        gate.set()
        _poll(client, f"/api/v1/tasks/{task_id}", {"cancelled"})
        status = client.get(f"/api/v1/tasks/{task_id}").json()
        assert status["status"] == "cancelled"
        assert status["finished_at"] is not None

        trace = client.get(f"/api/v1/ingestions/{task_id}/trace")
        assert trace.status_code == 200, trace.text
        body = trace.json()
        assert body["status"] == "canceled"
        assert body["retryable"] is True
        assert body["cancelable"] is False
        # the completed pre-cancel stage is still surfaced
        assert any(s["name"] == "load" for s in body["stages"])

    def test_repeated_cancel_is_idempotent(self, tmp_path):
        gate = threading.Event()
        services = build_batch3_services(tmp_path, pipeline=_GatedPipeline(gate))
        client = TestClient(create_app(services=services))
        task_id = client.post(
            f"/api/v1/collections/{_cid()}/documents",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        ).json()["task_id"]
        _poll(client, f"/api/v1/tasks/{task_id}", {"running"})

        assert client.post(f"/api/v1/tasks/{task_id}/cancel").status_code == 202
        assert client.post(f"/api/v1/tasks/{task_id}/cancel").status_code == 202
        gate.set()
        _poll(client, f"/api/v1/tasks/{task_id}", {"cancelled"})

    def test_cancel_after_complete_returns_409(self, tmp_path):
        services = build_batch3_services(tmp_path, pipeline=FakePipeline())
        client = TestClient(create_app(services=services))
        task_id = client.post(
            f"/api/v1/collections/{_cid()}/documents",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        ).json()["task_id"]
        _poll(client, f"/api/v1/tasks/{task_id}", {"succeeded"})

        resp = client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "TASK_NOT_CANCELABLE"
        # No side effects: still succeeded.
        assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "succeeded"

    def test_cancel_unknown_task_404(self, tmp_path):
        client = TestClient(create_app(services=build_batch3_services(tmp_path)))
        resp = client.post("/api/v1/tasks/00000000-0000-0000-0000-000000000000/cancel")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"

    def test_queued_pending_cancel(self, tmp_path):
        from src.application.services.task_tracker import TaskTracker
        from src.application.services.ingestion_service import IngestionService

        bm25 = tmp_path / "db" / "bm25"
        bm25.mkdir(parents=True, exist_ok=True)
        (bm25 / "default.json").write_text('{"n_docs":0,"avgdl":0.0,"k1":1.5,"b":0.75,"terms":{}}')
        tracker = TaskTracker()
        svc = IngestionService(
            FakePipeline(), upload_dir=tmp_path / "uploads", tracker=tracker,
        )
        rec = tracker.create(
            document_id=collection_uuid("default"),
            collection_id=collection_uuid("default"),
            source_path=str(tmp_path / "uploads" / "default" / "a.pdf"),
            filename="a.pdf",
        )
        # Queued = still pending; cancel transitions straight to terminal.
        done = svc.cancel(rec.id)
        assert done is not None and done.status == "cancelled"
        assert svc.get_task(rec.id).status == "cancelled"


# ---------------------------------------------------------------------------
# B3.3 — retry
# ---------------------------------------------------------------------------

class TestRetry:
    def _seed_failed_task(self, services, *, with_file: bool = True):
        svc = services.ingestion
        canonical = svc.compute_source_path("default", "a.pdf")
        canonical.parent.mkdir(parents=True, exist_ok=True)
        if with_file:
            canonical.write_bytes(b"%PDF-1.4 original")
        rec = svc.tracker.create(
            document_id=collection_uuid("default"),
            collection_id=collection_uuid("default"),
            source_path=str(canonical),
            filename="a.pdf",
        )
        svc.tracker.update(
            rec.id, lambda r: r.mark_failed(
                TaskError(code="INTERNAL_ERROR", message="boom", details={}),
            ),
        )
        return rec

    def test_retry_creates_child_with_attempt_and_parent_trace(self, tmp_path):
        services = build_batch3_services(tmp_path, pipeline=FakePipeline())
        client = TestClient(create_app(services=services))
        parent = self._seed_failed_task(services)

        resp = client.post(f"/api/v1/tasks/{parent.id}/retry")
        assert resp.status_code == 202, resp.text
        child = resp.json()
        assert child["attempt"] == 1
        assert child["id"] != str(parent.id)

        # Parent task is left intact.
        assert client.get(f"/api/v1/tasks/{parent.id}").json()["status"] == "failed"

        # Child trace is linked to the parent trace via parent_trace_id.
        child_trace = client.get(f"/api/v1/ingestions/{child['id']}/trace")
        assert child_trace.status_code == 200, child_trace.text
        body = child_trace.json()
        assert body["parent_trace_id"] == str(parent.id)
        assert body["attempt"] == 1

    def test_retry_rejects_allowed_state_only(self, tmp_path):
        services = build_batch3_services(tmp_path, pipeline=FakePipeline())
        client = TestClient(create_app(services=services))
        task_id = client.post(
            f"/api/v1/collections/{_cid()}/documents",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        ).json()["task_id"]
        _poll(client, f"/api/v1/tasks/{task_id}", {"succeeded"})

        # succeed is not retryable → stable 409.
        resp = client.post(f"/api/v1/tasks/{task_id}/retry")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "TASK_NOT_RETRYABLE"

    def test_retry_missing_source_file_404(self, tmp_path):
        services = build_batch3_services(tmp_path, pipeline=FakePipeline())
        client = TestClient(create_app(services=services))
        parent = self._seed_failed_task(services, with_file=False)

        resp = client.post(f"/api/v1/tasks/{parent.id}/retry")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SOURCE_FILE_MISSING"

    def test_retry_is_idempotent(self, tmp_path):
        services = build_batch3_services(tmp_path, pipeline=FakePipeline())
        client = TestClient(create_app(services=services))
        parent = self._seed_failed_task(services)

        first = client.post(f"/api/v1/tasks/{parent.id}/retry")
        second = client.post(f"/api/v1/tasks/{parent.id}/retry")
        assert first.status_code == 202 and second.status_code == 202
        assert first.json()["id"] == second.json()["id"]

    def test_retry_concurrent_is_idempotent(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor

        services = build_batch3_services(tmp_path, pipeline=FakePipeline())
        client = TestClient(create_app(services=services))
        parent = self._seed_failed_task(services)

        def do_retry(_):
            return client.post(f"/api/v1/tasks/{parent.id}/retry").json()["id"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(do_retry, range(8)))
        assert len(set(ids)) == 1  # exactly one child task created

    def test_retry_unknown_task_404(self, tmp_path):
        client = TestClient(create_app(services=build_batch3_services(tmp_path)))
        resp = client.post("/api/v1/tasks/00000000-0000-0000-0000-000000000000/retry")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"