"""
Unit tests for ``TraceService`` (G5/G6).

The service is a thin reader over ``logs/traces.jsonl`` —
exercising it means writing a tiny JSONL fixture and
asserting the parsed shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.observability.dashboard.services.trace_service import (
    Stage,
    StageEvent,
    TRACE_TYPE_INGESTION,
    TRACE_TYPE_QUERY,
    TraceRecord,
    TraceService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _ingestion_record(trace_id: str = "ing-001") -> dict:
    return {
        "trace_id": trace_id,
        "trace_type": TRACE_TYPE_INGESTION,
        "started_at": 1000.0,
        "finished_at": 1000.5,
        "total_elapsed_ms": 500.0,
        "stages": {
            "load": {
                "elapsed_ms": 100.0,
                "events": [
                    {"event": "start", "elapsed_ms": None},
                    {"event": "finish", "elapsed_ms": 100.0,
                     "n_in": 1, "method": "pymupdf"},
                ],
            },
            "split": {
                "elapsed_ms": 50.0,
                "events": [
                    {"event": "finish", "elapsed_ms": 50.0, "n_out": 10},
                ],
            },
        },
    }


def _query_record(
    trace_id: str = "q-001",
    query_text: str = "what is rag?",
    n_dense: int = 5,
    n_sparse: int = 3,
    rerank_fallback: bool = False,
) -> dict:
    rerank_events = [
        {"event": "fallback", "elapsed_ms": 0.0, "reason": "backend error"}
        if rerank_fallback
        else {"event": "finish", "elapsed_ms": 12.0, "n_in": 5, "n_out": 5},
    ]
    return {
        "trace_id": trace_id,
        "trace_type": TRACE_TYPE_QUERY,
        "started_at": 2000.0,
        "finished_at": 2000.3,
        "total_elapsed_ms": 300.0,
        "stages": {
            "dense_retrieval": {
                "elapsed_ms": 100.0,
                "events": [
                    {"event": "finish", "elapsed_ms": 100.0,
                     "n_in": 1, "n_out": n_dense, "query": query_text},
                ],
            },
            "sparse_retrieval": {
                "elapsed_ms": 50.0,
                "events": [
                    {"event": "finish", "elapsed_ms": 50.0,
                     "n_in": 1, "n_out": n_sparse, "query": query_text},
                ],
            },
            "rerank": {
                "elapsed_ms": 12.0,
                "events": rerank_events,
            },
        },
    }


@pytest.fixture
def trace_log(tmp_path: Path) -> Path:
    p = tmp_path / "traces.jsonl"
    _write_jsonl(p, [
        _ingestion_record("ing-001"),
        _ingestion_record("ing-002"),
        _query_record("q-001", "what is rag?"),
        _query_record("q-002", "hybrid search"),
    ])
    return p


@pytest.fixture
def svc(trace_log: Path) -> TraceService:
    return TraceService(trace_log)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestListRecent:
    def test_returns_all_when_no_filter(self, svc):
        recs = svc.list_recent()
        assert len(recs) == 4

    def test_newest_first(self, svc):
        recs = svc.list_recent()
        # started_at values: 1000, 1000, 2000, 2000
        # Newest first means the 2000s come first
        assert recs[0].trace_id.startswith("q-")

    def test_filter_by_ingestion(self, svc):
        recs = svc.list_recent(trace_type=TRACE_TYPE_INGESTION)
        assert all(r.trace_type == TRACE_TYPE_INGESTION for r in recs)
        assert len(recs) == 2

    def test_filter_by_query(self, svc):
        recs = svc.list_recent(trace_type=TRACE_TYPE_QUERY)
        assert len(recs) == 2
        assert all(r.trace_type == TRACE_TYPE_QUERY for r in recs)

    def test_limit(self, svc):
        recs = svc.list_recent(limit=1)
        assert len(recs) == 1


class TestGet:
    def test_get_existing(self, svc):
        rec = svc.get("q-001")
        assert rec is not None
        assert rec.trace_id == "q-001"

    def test_get_missing_returns_none(self, svc):
        assert svc.get("nope") is None


class TestSearch:
    def test_search_by_keyword_matches_query(self, svc):
        recs = svc.search(trace_type=TRACE_TYPE_QUERY, keyword="hybrid")
        assert len(recs) == 1
        assert recs[0].trace_id == "q-002"

    def test_search_by_keyword_matches_stage_name(self, svc):
        recs = svc.search(trace_type=TRACE_TYPE_QUERY, keyword="rerank")
        assert len(recs) == 2  # both queries have a rerank stage

    def test_search_no_match(self, svc):
        assert svc.search(keyword="nonsensical_xyz") == []


class TestParsing:
    def test_stages_have_events(self, svc):
        rec = svc.get("ing-001")
        assert rec is not None
        assert len(rec.stages) == 2
        load = next(s for s in rec.stages if s.name == "load")
        assert len(load.events) == 2
        assert load.events[0].event == "start"
        assert load.events[1].event == "finish"
        assert load.events[1].details.get("method") == "pymupdf"

    def test_event_details_parsed(self, svc):
        rec = svc.get("q-001")
        dense = next(s for s in rec.stages if s.name == "dense_retrieval")
        finish = dense.events[0]
        assert finish.details.get("query") == "what is rag?"
        assert finish.details.get("n_out") == 5

    def test_rerank_fallback_event(self, trace_log: Path):
        # Add a query record with a fallback event
        _write_jsonl(
            trace_log,
            [_query_record("q-fb", rerank_fallback=True)],
        )
        svc = TraceService(trace_log)
        rec = svc.get("q-fb")
        rerank = next(s for s in rec.stages if s.name == "rerank")
        assert rerank.events[0].event == "fallback"
        assert "reason" in rerank.events[0].details


class TestEdgeCases:
    def test_missing_log_file_returns_empty(self, tmp_path: Path):
        svc = TraceService(tmp_path / "nonexistent.jsonl")
        assert svc.list_recent() == []

    def test_empty_log_file(self, tmp_path: Path):
        log = tmp_path / "traces.jsonl"
        log.write_text("", encoding="utf-8")
        svc = TraceService(log)
        assert svc.list_recent() == []

    def test_malformed_lines_are_skipped(self, tmp_path: Path):
        log = tmp_path / "traces.jsonl"
        log.write_text(
            "this is not json\n"
            + json.dumps(_ingestion_record("good-1")) + "\n"
            + "{incomplete\n"
            + json.dumps(_query_record("q-ok")) + "\n",
            encoding="utf-8",
        )
        svc = TraceService(log)
        recs = svc.list_recent()
        # 2 valid lines survived.
        assert {r.trace_id for r in recs} == {"good-1", "q-ok"}

    def test_total_elapsed_ms_default_zero(self, tmp_path: Path):
        log = tmp_path / "traces.jsonl"
        log.write_text(
            json.dumps({
                "trace_id": "x",
                "trace_type": "query",
                "started_at": 1.0,
                # No finished_at, no total_elapsed_ms
                "stages": {},
            }) + "\n",
            encoding="utf-8",
        )
        svc = TraceService(log)
        rec = svc.get("x")
        assert rec.finished_at is None
        assert rec.total_elapsed_ms == 0.0
