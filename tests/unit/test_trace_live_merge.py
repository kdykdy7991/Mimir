"""B3.2 — live-task trace consistency.

- final/terminal state wins and never regresses;
- live (running) + recorded (terminal) merge;
- missing-trace fallback carries the task status;
- ``TraceStore.upsert_live`` keeps the in-memory timeline monotonic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.application.services.task_tracker import TaskRecord
from src.application.services.trace_store import TraceStore
from src.core.trace.trace_context import TRACE_TYPE_INGESTION, TraceContext
from src.web_api.mappers import build_live_trace_response, to_trace_response

PARENT = UUID("8e761e43-6cb6-437c-b618-42877b4c3618")


def _task(status: str = "running", *, attempt: int = 0, parent=None) -> TaskRecord:
    now = datetime.now(tz=timezone.utc)
    return TaskRecord(
        id=PARENT,
        document_id=UUID("9c2e1a40-0000-4000-8000-000000000001"),
        collection_id=UUID("5b6a0e00-0000-4000-8000-000000000002"),
        source_path="/uploads/default/a.pdf",
        filename="a.pdf",
        status=status,
        attempt=attempt,
        parent_task_id=parent,
        created_at=now,
        updated_at=now,
        finished_at=None if status in ("pending", "running") else now,
    )


def _raw(status: str | None = None, *, finished: bool = False, **stages) -> dict:
    started = 1_789_030_798.20
    trace = TraceContext(
        trace_id=str(PARENT), trace_type=TRACE_TYPE_INGESTION,
        started_at=started,
    )
    for name, extra in stages.items():
        trace.record_stage(name, ts=started, elapsed_ms=5.0, **extra)
    if status is not None:
        trace.status = status
    if finished or status in ("success", "failed", "canceled", "skipped"):
        trace.finish()
    return trace.to_dict()


class TestMissingTraceFallback:
    def test_terminal_task_without_trace_returns_status(self):
        resp = build_live_trace_response(_task("succeeded"), None)
        assert resp.status == "success"
        assert resp.stages == []
        assert resp.retryable is False
        assert resp.cancelable is False

    def test_failed_task_without_trace(self):
        resp = build_live_trace_response(_task("failed"), None)
        assert resp.status == "failed"
        assert resp.retryable is True

    def test_cancelled_task_without_trace(self):
        resp = build_live_trace_response(_task("cancelled"), None)
        assert resp.status == "canceled"
        assert resp.retryable is True


class TestLiveRunningTask:
    def test_terminal_trace_wins_over_live_task(self):
        # A stored terminal trace must not be downgraded to running by a
        # stale live snapshot of the same task.
        task = _task("succeeded")
        raw = _raw(status="success", load={})
        resp = build_live_trace_response(task, raw)
        assert resp.status == "success"
        assert resp.cancelable is False
        # no synthetic running current-stage is appended for a terminal task
        assert all(s.status != "running" for s in resp.stages)

    def test_live_task_appends_current_stage(self):
        from src.application.services.task_types import TaskProgress

        task = _task("running")
        task.progress = TaskProgress(stage="embed", current=1, total=2, percent=50)
        raw = _raw(load={}, split={})  # completed stages only
        resp = build_live_trace_response(task, raw)
        assert resp.status == "running"
        assert resp.cancelable is True
        names = [s.name for s in resp.stages]
        assert names == ["load", "split", "embed"]
        assert resp.stages[-1].status == "running"

    def test_missing_trace_live_task_still_lists_current_stage(self):
        from src.application.services.task_types import TaskProgress

        task = _task("running")
        task.progress = TaskProgress(stage="load", current=1, total=1, percent=100)
        resp = build_live_trace_response(task, None)
        assert resp.status == "running"
        assert [s.name for s in resp.stages] == ["load"]
        assert resp.stages[-1].status == "running"


class TestMonotonicStore:
    def test_stage_status_never_regresses(self, tmp_path):
        store = TraceStore(log_path=tmp_path / "traces.jsonl")
        live = TraceContext(trace_id=str(PARENT), trace_type=TRACE_TYPE_INGESTION)
        live.record_stage("embed", elapsed_ms=5.0, status="success")
        store.upsert_live(live)

        # A later, stale snapshot tries to re-report embed as running → ignored.
        stale = TraceContext(trace_id=str(PARENT), trace_type=TRACE_TYPE_INGESTION)
        stale.record_stage("embed", status="running")
        store.upsert_live(stale)

        payload = store.get(str(PARENT))
        assert payload is not None
        embed = next(s for s in payload["stages"] if s["name"] == "embed")
        assert embed["status"] == "success"

    def test_finished_trace_never_cleared(self, tmp_path):
        store = TraceStore(log_path=tmp_path / "traces.jsonl")
        done = TraceContext(trace_id=str(PARENT), trace_type=TRACE_TYPE_INGESTION)
        done.finish()
        store.record(done)
        finished = store.get(str(PARENT))["finished_at"]

        live = TraceContext(trace_id=str(PARENT), trace_type=TRACE_TYPE_INGESTION)
        store.upsert_live(live)
        assert store.get(str(PARENT))["finished_at"] == finished


class TestTerminalMerge:
    def test_failed_trace_is_retryable_and_not_cancelable(self):
        task = _task("failed")
        raw = _raw(status="failed", embed={"event": "error"})
        resp = build_live_trace_response(task, raw)
        assert resp.status == "failed"
        assert resp.retryable is True
        assert resp.cancelable is False

    def test_child_trace_carries_parent_and_attempt(self):
        task = _task("failed", attempt=2, parent=PARENT)
        raw = _raw(status="failed", embed={"event": "error"})
        resp = build_live_trace_response(task, raw)
        assert resp.attempt == 2
        assert resp.parent_trace_id == str(PARENT)


def test_to_trace_response_reads_metadata_attempt_fallback():
    raw = _raw(status="failed", embed={})
    raw["metadata"]["attempt"] = 3
    raw["metadata"]["parent_trace_id"] = str(PARENT)
    resp = to_trace_response(raw)
    assert resp.attempt == 3
    assert resp.parent_trace_id == str(PARENT)