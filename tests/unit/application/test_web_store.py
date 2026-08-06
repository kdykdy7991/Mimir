"""
Unit tests for the M3 batch 2 SQLite persistence layer.

Covers ``WebApiDB`` (tasks / traces / query results + citations) and the
``TaskTracker`` durable wiring (task_type, write-through, restart
recovery) at the unit level; the end-to-end behaviour is exercised by
``tests/integration/test_web_api_m3_batch2.py``.
"""

from __future__ import annotations

import pytest

from src.application.services.task_tracker import TaskTracker
from src.application.services.web_store import WebApiDB
from src.web_api.schemas.errors import TaskError
from src.web_api.schemas.tasks import TaskProgress


@pytest.fixture
def db(tmp_path) -> WebApiDB:
    return WebApiDB(tmp_path / "db" / "web_api.db")


# ---------------------------------------------------------------------------
# WebApiDB
# ---------------------------------------------------------------------------

class TestWebApiDB:
    def test_collection_description_survives_restart(self, tmp_path) -> None:
        path = tmp_path / "db" / "web_api.db"
        WebApiDB(path).upsert_collection("reports", "quarterly")

        row = WebApiDB(path).get_collection("reports")

        assert row is not None
        assert row["description"] == "quarterly"

    def test_delete_collection_metadata(self, db: WebApiDB) -> None:
        db.upsert_collection("reports", "quarterly")
        assert db.delete_collection("reports") is True
        assert db.get_collection("reports") is None

    def test_task_round_trip_across_instances(self, tmp_path) -> None:
        path = tmp_path / "db" / "web_api.db"
        db1 = WebApiDB(path)
        db1.upsert_task({
            "task_id": "t1", "task_type": "ingestion",
            "collection_id": "c1", "source_path": "/a.pdf",
            "status": "succeeded", "attempt": 1,
            "created_at": 1.0, "updated_at": 2.0,
        })
        # A brand-new instance on the same file = a process restart.
        db2 = WebApiDB(path)
        row = db2.get_task("t1")
        assert row is not None
        assert row["task_id"] == "t1"
        assert row["status"] == "succeeded"

    def test_trace_round_trip(self, db: WebApiDB) -> None:
        db.upsert_trace({"trace_id": "tr1", "trace_type": "query", "started_at": 5.0})
        got = db.get_trace("tr1")
        assert got is not None
        assert got["trace_type"] == "query"

    def test_query_citation_lookup(self, db: WebApiDB) -> None:
        db.save_query_result(
            query_id="q1", collection="default", query_text="hello",
            result_json='{"chunks": []}', document_ids=["d1", "d2"],
        )
        db.save_query_result(
            query_id="q2", collection="default", query_text="later",
            result_json='{"chunks": []}', document_ids=["d1"],
        )
        # Most recent query citing d1 is q2.
        assert db.last_query_for_document("d1") == "q2"
        assert db.last_query_for_document("d2") == "q1"
        assert db.last_query_for_document("nope") is None
        # Result rows are readable.
        assert db.get_query_result("q1")["query_text"] == "hello"


# ---------------------------------------------------------------------------
# TaskTracker durable wiring
# ---------------------------------------------------------------------------

class TestTaskTrackerPersistence:
    def test_write_through_and_restart_recovery(self, db: WebApiDB) -> None:
        tracker1 = TaskTracker(db=db)
        rec = tracker1.create(
            task_id="11111111-1111-1111-1111-111111111111",
            task_type="query",
            document_id=None,
            collection_id="22222222-2222-2222-2222-222222222222",
            source_path="", filename="",
        )
        tracker1.update(rec.id, lambda r: r.mark_started())
        tracker1.update(rec.id, lambda r: r.mark_succeeded())

        # "Restart": a fresh tracker over the same db still sees the task.
        tracker2 = TaskTracker(db=db)
        got = tracker2.get(rec.id)
        assert got is not None
        assert got.task_type == "query"
        assert got.status == "succeeded"

    def test_task_type_defaults_to_ingestion(self, db: WebApiDB) -> None:
        tracker = TaskTracker(db=db)
        rec = tracker.create(
            document_id="33333333-3333-3333-3333-333333333333",
            collection_id="22222222-2222-2222-2222-222222222222",
            source_path="/a.pdf", filename="a.pdf",
        )
        assert rec.task_type == "ingestion"

    def test_error_and_progress_round_trip(self, db: WebApiDB) -> None:
        tracker = TaskTracker(db=db)
        rec = tracker.create(
            document_id="33333333-3333-3333-3333-333333333333",
            collection_id="22222222-2222-2222-2222-222222222222",
            source_path="/a.pdf", filename="a.pdf",
        )
        tracker.update(rec.id, lambda r: r.update_progress("load", 1, 1, 50))
        tracker.update(rec.id, lambda r: r.mark_failed(
            TaskError(code="UPSTREAM_ERROR", message="boom", details={}),
        ))

        tracker2 = TaskTracker(db=db)
        got = tracker2.get(rec.id)
        assert got is not None
        assert got.status == "failed"
        assert got.error is not None
        assert got.error.code == "UPSTREAM_ERROR"
        assert got.progress is not None
        assert isinstance(got.progress, TaskProgress)


__all__ = []
