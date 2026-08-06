"""
Unit tests for the application-layer ``IngestionService``.

Sits alongside the dashboard's ``test_ingestion_service.py`` (G4,
which wraps ``scripts.ingest.build_pipeline`` with temp-file staging);
this one covers the thin ``src.application.services.IngestionService``
facade over ``IngestionPipeline.run``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

import pytest

from src.application.services import IngestionService


@dataclass
class _FakePipelineResult:
    skipped: bool = False
    n_chunks: int = 2


class _FakePipeline:
    """Records ``run`` calls and returns a fixed result."""

    def __init__(self, *, skipped: bool = False) -> None:
        self.skipped = skipped
        self.calls: list[dict] = []

    def run(self, path: str, *, on_progress=None, trace=None, collection=None, source_path=None) -> _FakePipelineResult:
        self.calls.append({
            "path": path,
            "on_progress": on_progress,
            "trace": trace,
            "collection": collection,
            "source_path": source_path,
        })
        return _FakePipelineResult(skipped=self.skipped)


class _FakeEngineCache:
    """Records which collections are requested; returns per-collection fakes."""

    def __init__(self) -> None:
        self.requested: list[str] = []
        self.pipelines: dict[str, _FakePipeline] = {}

    def pipeline_for(self, collection: str) -> _FakePipeline:
        self.requested.append(collection)
        if collection not in self.pipelines:
            self.pipelines[collection] = _FakePipeline()
        return self.pipelines[collection]


@pytest.fixture
def pipeline() -> _FakePipeline:
    return _FakePipeline()


@pytest.fixture
def svc(pipeline: _FakePipeline) -> IngestionService:
    return IngestionService(pipeline)


class TestConstruction:
    def test_exposes_collaborator(self, pipeline: _FakePipeline) -> None:
        assert IngestionService(pipeline).pipeline is pipeline


class TestIngest:
    def test_delegates_with_defaults(
        self, svc: IngestionService, pipeline: _FakePipeline,
    ) -> None:
        result = svc.ingest("/tmp/a.pdf")
        assert result.n_chunks == 2
        call = pipeline.calls[0]
        assert call["path"] == "/tmp/a.pdf"
        assert call["on_progress"] is None
        assert call["trace"] is None

    def test_passes_progress_and_trace(
        self, svc: IngestionService, pipeline: _FakePipeline,
    ) -> None:
        on_progress = lambda stage, cur, tot: None  # noqa: E731
        trace = object()
        svc.ingest("/x.pdf", on_progress=on_progress, trace=trace)
        call = pipeline.calls[0]
        assert call["on_progress"] is on_progress
        assert call["trace"] is trace


def _wait_terminal(
    svc: IngestionService, task_id, timeout: float = 2.0,
):
    """Poll the tracker until the worker flips the task to a terminal state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = svc.get_task(task_id)
        if rec is not None and rec.status in ("succeeded", "failed", "skipped"):
            return rec
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not finish within {timeout}s")


class TestEngineCacheRouting:
    """M3: a cache-backed IngestionService routes ``upload`` by collection."""

    def test_pipeline_property_rejects_cache_backed_service(self) -> None:
        svc = IngestionService(_FakeEngineCache())
        with pytest.raises(TypeError):
            svc.pipeline

    def test_ingest_routes_default_collection(self, tmp_path) -> None:
        cache = _FakeEngineCache()
        svc = IngestionService(cache, upload_dir=tmp_path)
        svc.ingest("/tmp/a.pdf")
        assert cache.requested == ["default"]

    def test_upload_routes_pipeline_by_collection(self, tmp_path) -> None:
        cache = _FakeEngineCache()
        svc = IngestionService(cache, upload_dir=tmp_path)
        record = svc.upload(
            bytes_payload=b"%PDF-1.4",
            filename="a.pdf",
            collection="reports",
            collection_id=uuid4(),
            document_id=uuid4(),
        )
        _wait_terminal(svc, record.id)
        # The worker resolved the "reports" pipeline, not "default".
        assert cache.requested == ["reports"]
        pipeline = cache.pipelines["reports"]
        assert len(pipeline.calls) == 1
        # M5: the worker ingests a per-task temp file under the collection's
        # .tmp dir (keeping the original suffix so the loader dispatches).
        path = pipeline.calls[0]["path"]
        assert "reports/.tmp" in path
        assert path.endswith("-a.pdf")
        # ...but labels the document with the stable canonical source_path.
        canonical = svc.compute_source_path("reports", "a.pdf")
        assert pipeline.calls[0]["source_path"] == str(canonical)
        # M3 regression: the collection must be forwarded to ``run()``
        # so the pipeline's integrity / image writes are scoped to the
        # right collection — without this, a non-default upload was
        # labelled "default" in those stores (documents showed up under
        # the wrong knowledge base).
        assert pipeline.calls[0]["collection"] == "reports"

    def test_upload_to_distinct_collections_routes_separately(
        self, tmp_path,
    ) -> None:
        cache = _FakeEngineCache()
        svc = IngestionService(cache, upload_dir=tmp_path)
        for name in ("a", "b"):
            record = svc.upload(
                bytes_payload=b"%PDF-1.4",
                filename=f"{name}.pdf",
                collection=name,
                collection_id=uuid4(),
                document_id=uuid4(),
            )
            _wait_terminal(svc, record.id)
        assert cache.requested == ["a", "b"]
        assert cache.pipelines["a"].calls
        assert cache.pipelines["b"].calls
        assert cache.pipelines["a"] is not cache.pipelines["b"]


class TestSkippedTask:
    """M5: a pipeline run that short-circuits (duplicate) marks the task skipped."""

    def test_worker_marks_task_skipped_when_pipeline_skips(self, tmp_path) -> None:
        svc = IngestionService(_FakePipeline(skipped=True), upload_dir=tmp_path)
        record = svc.upload(
            bytes_payload=b"%PDF-1.4",
            filename="dup.pdf",
            collection="default",
            collection_id=uuid4(),
            document_id=uuid4(),
        )
        finished = _wait_terminal(svc, record.id)
        assert finished.status == "skipped"
        assert finished.finished_at is not None
        assert finished.progress is None
        assert finished.error is None

    def test_worker_marks_task_succeeded_when_pipeline_runs(self, tmp_path) -> None:
        svc = IngestionService(_FakePipeline(skipped=False), upload_dir=tmp_path)
        record = svc.upload(
            bytes_payload=b"%PDF-1.4",
            filename="ok.pdf",
            collection="default",
            collection_id=uuid4(),
            document_id=uuid4(),
        )
        finished = _wait_terminal(svc, record.id)
        assert finished.status == "succeeded"


__all__ = []
