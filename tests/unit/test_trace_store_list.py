"""B3.5 — bounded ``TraceStore.list`` with filters, cursor pagination and
SQLite-index-backed querying (never an unbounded JSONL scan)."""

import json

from src.application.services.trace_store import TraceStore
from src.core.trace.trace_context import TRACE_TYPE_INGESTION, TraceContext

COLL_A = "11111111-1111-4111-8111-111111111111"
COLL_B = "22222222-2222-4222-8222-222222222222"
DOC_A1 = "33333333-3333-4333-8333-333333333333"
DOC_A2 = "44444444-4444-4444-8444-444444444444"


def _trace(trace_id: str, started: float, *, trace_type=TRACE_TYPE_INGESTION,
           status: str | None = None, collection=None, document=None) -> TraceContext:
    t = TraceContext(
        trace_id=trace_id, trace_type=trace_type, started_at=started,
        metadata={
            "collection_id": collection,
            "collection": "default" if collection else None,
            "document_id": document,
            "source_path": f"/uploads/{document or trace_id}.pdf",
            "filename": f"{trace_id}.pdf",
        },
    )
    t.record_stage("load", elapsed_ms=5.0)
    t.record_stage("embed", elapsed_ms=5.0)
    if status is not None:
        t.status = status
    t.finish()
    return t


def _seed(store: TraceStore) -> None:
    """Record 5 ingestion traces, several sharing collection/doc ids."""
    store.record(_trace("t-100", 100.0, collection=COLL_A, document=DOC_A1, status="success"))
    store.record(_trace("t-200", 200.0, collection=COLL_A, document=DOC_A1, status="failed"))
    store.record(_trace("t-300", 300.0, collection=COLL_B, document=DOC_A2, status="success"))
    store.record(_trace("t-400", 400.0, collection=COLL_A, document=DOC_A2, status="canceled"))
    store.record(_trace("t-500", 500.0, collection=COLL_B, document=DOC_A2, status="success"))


def test_lists_newest_first(tmp_path):
    store = TraceStore(log_path=tmp_path / "traces.jsonl")
    _seed(store)
    items, next_cursor = store.list(limit=20)
    assert next_cursor is None
    assert [i["trace_id"] for i in items] == ["t-500", "t-400", "t-300", "t-200", "t-100"]


def test_filters_by_collection_and_document(tmp_path):
    store = TraceStore(log_path=tmp_path / "traces.jsonl")
    _seed(store)
    items, _ = store.list(collection_id=COLL_B, limit=20)
    assert {i["trace_id"] for i in items} == {"t-500", "t-300"}
    items, _ = store.list(collection_id=COLL_A, document_id=DOC_A1, limit=20)
    assert {i["trace_id"] for i in items} == {"t-200", "t-100"}


def test_filters_by_status_and_type(tmp_path):
    store = TraceStore(log_path=tmp_path / "traces.jsonl")
    _seed(store)
    items, _ = store.list(status="failed", limit=20)
    assert [i["trace_id"] for i in items] == ["t-200"]
    items, _ = store.list(status="canceled", limit=20)
    assert [i["trace_id"] for i in items] == ["t-400"]
    items, _ = store.list(status="nonexistent", limit=20)
    assert items == []


def test_q_filter(tmp_path):
    store = TraceStore(log_path=tmp_path / "traces.jsonl")
    _seed(store)
    # q matches the document_id / filename text blob.
    items, _ = store.list(q=DOC_A1, limit=20)
    assert {i["trace_id"] for i in items} == {"t-200", "t-100"}
    items, _ = store.list(q="t-300", limit=20)
    assert [i["trace_id"] for i in items] == ["t-300"]


def test_empty_result(tmp_path):
    store = TraceStore(log_path=tmp_path / "traces.jsonl")
    _seed(store)
    items, next_cursor = store.list(collection_id="99999999-9999-4999-8999-999999999999", limit=20)
    assert items == []
    assert next_cursor is None


def test_cursor_pagination_is_stable(tmp_path):
    store = TraceStore(log_path=tmp_path / "traces.jsonl")
    _seed(store)
    seen: list[str] = []
    cursor = None
    while True:
        items, cursor = store.list(limit=2, cursor=cursor)
        ids = [i["trace_id"] for i in items]
        # newest-first order must hold with no duplicates across pages.
        assert len(set(ids)) == len(ids)
        assert sorted(ids, reverse=True) == ids  # stable ordering
        for tid in ids:
            assert tid not in seen
            seen.append(tid)
        if cursor is None:
            break
    assert seen == ["t-500", "t-400", "t-300", "t-200", "t-100"]
    assert len(seen) == 5


def test_page_does_not_shift_when_new_trace_appears(tmp_path):
    store = TraceStore(log_path=tmp_path / "traces.jsonl")
    _seed(store)
    _, cursor = store.list(limit=2)  # t-500,t-400 cached cursor
    store.record(_trace("t-600", 600.0, status="success"))
    # The keyset cursor keys on (started_at, trace_id) DESC, so t-500
    # (started 500) still appears after t-600 (started 600).
    items, _ = store.list(limit=2, cursor=cursor)
    assert [i["trace_id"] for i in items] == ["t-300", "t-200"]


def test_date_range_filter(tmp_path):
    store = TraceStore(log_path=tmp_path / "traces.jsonl")
    _seed(store)
    items, _ = store.list(started_from=250.0, started_to=450.0, limit=20)
    assert [i["trace_id"] for i in items] == ["t-400", "t-300"]
    items, _ = store.list(started_from=500.0 + 1e-9, limit=20)
    assert items == []


def test_list_does_not_scan_unbounded_jsonl(tmp_path):
    store = TraceStore(log_path=tmp_path / "traces.jsonl")
    _seed(store)
    # Poison the JSONL with a huge number of lines that are NOT in the index
    # (e.g. traces recorded by an older store pre-index). The list must
    # return only the indexed rows and must not read this file at all.
    with (tmp_path / "traces.jsonl").open("a") as f:
        for i in range(5000):
            f.write(json.dumps({
                "trace_id": f"bogus-{i}", "trace_type": "ingestion",
                "started_at": float(i), "finished_at": float(i) + 1.0,
            }) + "\n")

    items, _ = store.list(limit=20)
    assert len(items) == 5  # exactly the indexed records — JSONL untouched
    items_b, _ = store.list(collection_id=COLL_A, limit=20)
    assert {i["trace_id"] for i in items_b} == {"t-400", "t-200", "t-100"}


def test_get_still_falls_back_to_jsonl_scan(tmp_path):
    store = TraceStore(log_path=tmp_path / "traces.jsonl")
    # A trace only present in JSONL (simulating a pre-index record).
    with (tmp_path / "traces.jsonl").open("w") as f:
        f.write(json.dumps({
            "trace_id": "legacy-1", "trace_type": "ingestion",
            "started_at": 1.0, "finished_at": 2.0,
        }) + "\n")
    hit = store.get("legacy-1")
    assert hit is not None and hit["trace_id"] == "legacy-1"
    # ...but it is NOT returned by the index-backed list.
    items, _ = store.list(limit=20)
    assert items == []