"""
Application-layer unit tests for ``IngestionService.upload_batch`` (M5).

Uses fake pipelines so no real loader / embedder / store is touched.
Covers the batch contract:

- per-file independent validation → ``rejected`` entries never block
  the other files and never touch disk;
- dedup pre-check → ``skipped`` with no task / worker / file;
- accepted files each get their own task (and run through the same
  worker path as a single upload);
- response counters + ``get_batch``;
- ``on_complete`` fires once per accepted task.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from src.application.services import (
    BatchFileUpload,
    IngestionService,
    UploadPolicy,
)


@dataclass
class _PipelineResult:
    skipped: bool = False


class _FakeIntegrity:
    """``should_skip`` returns a fixed answer for every hash."""

    def __init__(self, skip: bool = False) -> None:
        self.skip = skip
        self.calls: list[tuple[str, str]] = []

    def should_skip(self, file_hash: str, *, collection: str = "default") -> bool:
        self.calls.append((file_hash, collection))
        return self.skip


class _FakePipeline:
    """Records ``run`` calls; optionally exposes ``file_integrity``."""

    def __init__(self, *, integrity: _FakeIntegrity | None = None) -> None:
        self.integrity = integrity
        self.runs: list[dict] = []

    @property
    def file_integrity(self):
        return self.integrity

    def run(self, path, *, on_progress=None, trace=None, collection=None,
            source_path=None) -> _PipelineResult:
        # Reads the file the worker gave us — lets tests prove each task
        # processed ITS OWN staged bytes (no cross-task overwrite).
        self.runs.append({
            "path": path,
            "collection": collection,
            "source_path": source_path,
            "bytes": Path(path).read_bytes(),
        })
        if on_progress is not None:
            on_progress("load", 1, 1)
            on_progress("upsert", 1, 1)
        return _PipelineResult()


class _WaitHook:
    """Per-task ``on_complete`` collector."""

    def __init__(self) -> None:
        self.events: dict[UUID, threading.Event] = {}
        self.completed: list[UUID] = []
        self.lock = threading.Lock()

    def __call__(self, record) -> None:
        with self.lock:
            self.completed.append(record.id)
        ev = self.events.get(record.id)
        if ev is not None:
            ev.set()


def _wait_terminal(svc, task_id: UUID, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = svc.get_task(task_id)
        if rec is not None and rec.status in ("succeeded", "failed", "skipped"):
            return rec
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach a terminal state")


def _wait_hooks(hook: _WaitHook, n: int, timeout: float = 3.0) -> None:
    """Wait until ``n`` ``on_complete`` calls fired (they run after cleanup)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with hook.lock:
            if len(hook.completed) >= n:
                return
        time.sleep(0.01)
    with hook.lock:
        got = len(hook.completed)
    raise AssertionError(f"expected {n} on_complete calls, got {got}")


def _make_service(tmp_path, *, integrity=None, upload_policy=None) -> IngestionService:
    pipeline = _FakePipeline(integrity=integrity)
    svc = IngestionService(
        pipeline,
        upload_dir=tmp_path / "uploads",
        upload_policy=upload_policy,
    )
    return svc, pipeline


def _item(name: str, *, mime: str = "application/pdf", payload: bytes | None = None) -> BatchFileUpload:
    return BatchFileUpload(
        filename=name,
        content_type=mime,
        bytes_payload=payload if payload is not None else f"content of {name}".encode(),
    )


class TestBatchAccepted:
    def test_all_files_accepted_with_own_task(self, tmp_path) -> None:
        svc, pipeline = _make_service(tmp_path)
        hook = _WaitHook()
        cid = uuid4()
        items = [_item("a.pdf"), _item("b.md", mime="text/markdown")]
        resp = svc.upload_batch(
            items=items,
            collection="default", collection_id=cid,
            on_complete=hook,
        )
        assert resp.total == 2
        assert resp.accepted == 2 and resp.skipped == 0 and resp.rejected == 0
        assert [f.status for f in resp.files] == ["accepted", "accepted"]
        for f in resp.files:
            assert f.task_id is not None
            assert f.document_id is not None
            assert f.error is None
            # each accepted file got its own worker run
            _wait_terminal(svc, f.task_id)
        assert len(pipeline.runs) == 2
        # M5: each worker ingested from its OWN unique temp path under
        # upload_dir/<collection>/.tmp/ and read exactly the staged bytes.
        assert all(".tmp" in r["path"] for r in pipeline.runs)
        assert {r["bytes"] for r in pipeline.runs} == {i.bytes_payload for i in items}
        # Staged temp files are cleaned up once the tasks are done (the
        # on_complete hooks fire after cleanup).
        _wait_hooks(hook, len(resp.files))
        assert list((tmp_path / "uploads" / "default" / ".tmp").glob("*")) == []

    def test_document_ids_are_stable_uuids(self, tmp_path) -> None:
        svc, _ = _make_service(tmp_path)
        cid = uuid4()
        resp1 = svc.upload_batch(items=[_item("same.pdf")], collection="default", collection_id=cid)
        resp2 = svc.upload_batch(items=[_item("same.pdf")], collection="default", collection_id=cid)
        assert resp1.files[0].document_id == resp2.files[0].document_id

    def test_on_complete_fires_once_per_accepted(self, tmp_path) -> None:
        svc, _ = _make_service(tmp_path)
        hook = _WaitHook()
        resp = svc.upload_batch(
            items=[_item("a.pdf"), _item("b.md", mime="text/markdown")],
            collection="default", collection_id=uuid4(), on_complete=hook,
        )
        _wait_hooks(hook, len(resp.files))
        assert set(hook.completed) == {f.task_id for f in resp.files}


class TestBatchRejected:
    def test_unsupported_extension_rejected(self, tmp_path) -> None:
        svc, pipeline = _make_service(tmp_path)
        resp = svc.upload_batch(
            items=[_item("ok.pdf"), _item("bad.xyz")],
            collection="default", collection_id=uuid4(),
        )
        assert resp.accepted == 1 and resp.rejected == 1
        rejected = next(f for f in resp.files if f.status == "rejected")
        assert rejected.filename == "bad.xyz"
        assert rejected.error is not None
        assert rejected.error.code == "UNSUPPORTED_FILE_TYPE"
        assert rejected.task_id is None and rejected.document_id is None
        # rejected file never reached disk / worker
        assert not (tmp_path / "uploads" / "default" / "bad.xyz").exists()
        accepted = next(f for f in resp.files if f.status == "accepted")
        _wait_terminal(svc, accepted.task_id)
        assert len(pipeline.runs) == 1

    def test_unsupported_mime_rejected(self, tmp_path) -> None:
        svc, _ = _make_service(tmp_path)
        resp = svc.upload_batch(
            items=[_item("a.pdf", mime="application/octet-stream")],
            collection="default", collection_id=uuid4(),
        )
        assert resp.files[0].status == "rejected"
        assert resp.files[0].error.code == "UNSUPPORTED_MEDIA_TYPE"

    def test_oversize_file_rejected(self, tmp_path) -> None:
        # Inject a tiny per-file cap via the application UploadPolicy.
        svc, _ = _make_service(tmp_path, upload_policy=UploadPolicy(max_file_bytes=4))
        resp = svc.upload_batch(
            items=[_item("big.pdf", payload=b"12345")],
            collection="default", collection_id=uuid4(),
        )
        assert resp.files[0].status == "rejected"
        assert resp.files[0].error.code == "FILE_TOO_LARGE"
        assert not (tmp_path / "uploads" / "default" / "big.pdf").exists()

    def test_duplicate_filename_in_batch_rejected(self, tmp_path) -> None:
        svc, pipeline = _make_service(tmp_path)
        resp = svc.upload_batch(
            items=[_item("a.pdf"), _item("a.pdf")],
            collection="default", collection_id=uuid4(),
        )
        assert resp.accepted == 1 and resp.rejected == 1
        rejected = next(f for f in resp.files if f.status == "rejected")
        assert rejected.error.code == "DUPLICATE_FILENAME"
        # Only the first copy spawned a worker (wait for it to finish).
        accepted = next(f for f in resp.files if f.status == "accepted")
        _wait_terminal(svc, accepted.task_id)
        assert len(pipeline.runs) == 1


class TestBatchSkipped:
    def test_dedup_precheck_marks_skipped_without_task_or_disk(self, tmp_path) -> None:
        integrity = _FakeIntegrity(skip=True)
        svc, pipeline = _make_service(tmp_path, integrity=integrity)
        resp = svc.upload_batch(
            items=[_item("dup.pdf")],
            collection="default", collection_id=uuid4(),
        )
        assert resp.skipped == 1 and resp.accepted == 0
        f = resp.files[0]
        assert f.status == "skipped"
        assert f.task_id is None
        assert f.document_id is not None  # resolves to the already-ingested doc
        assert f.error is None
        # no worker spawned, nothing written
        assert pipeline.runs == []
        assert len(svc.tracker) == 0
        assert not (tmp_path / "uploads" / "default" / "dup.pdf").exists()
        # pre-check consulted the integrity layer with the right collection
        assert integrity.calls and integrity.calls[0][1] == "default"

    def test_byte_identical_files_with_distinct_names_both_accepted(self, tmp_path) -> None:
        # Identical bytes under *different* names are not caught by the
        # pre-check (the pipeline has no file_integrity here) nor by the
        # duplicate-name guard — both are accepted; the per-collection
        # write lock makes the second worker skip in a real pipeline.
        svc, pipeline = _make_service(tmp_path)
        payload = b"# x"
        resp = svc.upload_batch(
            items=[
                _item("one.md", mime="text/markdown", payload=payload),
                _item("two.md", mime="text/markdown", payload=payload),
            ],
            collection="default", collection_id=uuid4(),
        )
        assert resp.accepted == 2 and resp.rejected == 0
        assert [f.status for f in resp.files] == ["accepted", "accepted"]
        for f in resp.files:
            _wait_terminal(svc, f.task_id)
        assert len(pipeline.runs) == 2


class TestBatchRegistry:
    def test_get_batch_returns_submission_snapshot(self, tmp_path) -> None:
        svc, _ = _make_service(tmp_path)
        resp = svc.upload_batch(items=[_item("a.pdf")], collection="default", collection_id=uuid4())
        fetched = svc.get_batch(resp.batch_id)
        assert fetched is not None
        assert fetched.batch_id == resp.batch_id
        assert fetched.total == 1
        assert svc.get_batch(uuid4()) is None


class TestConcurrentSameName:
    """M5 review fix: two concurrent batches with the SAME filename but
    different content must not overwrite each other's in-flight file."""

    def test_no_cross_task_overwrite_and_stable_identity(self, tmp_path) -> None:
        svc, pipeline = _make_service(tmp_path)
        cid = uuid4()
        barrier = threading.Barrier(2)
        results: dict[str, object] = {}

        def submit(tag: str, content: bytes) -> None:
            barrier.wait()  # both batches submit at the same instant
            results[tag] = svc.upload_batch(
                items=[BatchFileUpload(
                    filename="same.pdf",
                    content_type="application/pdf",
                    bytes_payload=content,
                )],
                collection="default", collection_id=cid,
            )

        threads = [
            threading.Thread(target=submit, args=("a", b"CONTENT-AAA")),
            threading.Thread(target=submit, args=("b", b"CONTENT-BBB")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for tag in ("a", "b"):
            resp = results[tag]  # type: ignore[assignment]
            assert resp.accepted == 1, resp
            _wait_terminal(svc, resp.files[0].task_id)

        # Each worker read ITS OWN bytes — the old stable-path write would
        # have raced here (one worker could see the other's content).
        assert {r["bytes"] for r in pipeline.runs} == {b"CONTENT-AAA", b"CONTENT-BBB"}
        # Workers ran on distinct per-task temp paths.
        assert len({r["path"] for r in pipeline.runs}) == 2
        # Document identity stays stable: both tasks carry the same
        # canonical source_path.
        canonical = {r["source_path"] for r in pipeline.runs}
        assert len(canonical) == 1
        assert canonical == {str(svc.compute_source_path("default", "same.pdf"))}
