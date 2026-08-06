"""
Unit tests for TraceContext + TraceCollector (F1).

Covers:
- Construction: trace_type validation, defaults
- record_stage: events carry name/ts/extra data
- finish: idempotent, sets finished_at
- elapsed_ms:
  - total = finished_at - started_at (or now)
  - per-stage = last.ts - first.ts, 0 if single event,
    -1.0 if missing
- to_dict: required fields, total_elapsed_ms derived,
  JSON-serialisable
- TraceCollector: writes one JSONL line per trace, finishes
  in-flight traces, creates parent dirs, default path
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.core.trace.trace_context import (
    DEFAULT_LOG_PATH,
    TraceCollector,
    TraceContext,
    new_trace,
)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_default_trace_type_is_ingestion():
    t = TraceContext()
    assert t.trace_type == "ingestion"
    assert t.finished_at is None
    assert t.stages == []


def test_explicit_trace_type_accepted():
    for tt in ("ingestion", "query"):
        t = TraceContext(trace_type=tt)
        assert t.trace_type == tt


def test_invalid_trace_type_rejected():
    with pytest.raises(ValueError, match="trace_type"):
        TraceContext(trace_type="bogus")


def test_trace_id_is_unique():
    ids = {TraceContext().trace_id for _ in range(50)}
    assert len(ids) == 50


def test_new_trace_helper():
    t = new_trace(trace_type="query")
    assert t.trace_type == "query"


# ---------------------------------------------------------------------------
# record_stage
# ---------------------------------------------------------------------------

def test_record_stage_appends_event():
    t = TraceContext()
    t.record_stage("a", event="start")
    t.record_stage("a", event="finish", n=3)
    assert len(t.stages) == 2
    assert t.stages[0]["name"] == "a"
    assert t.stages[0]["event"] == "start"
    assert t.stages[1]["n"] == 3
    # timestamps are wall-clock floats
    assert all(isinstance(e["ts"], float) for e in t.stages)
    # ordering preserved
    assert t.stages[0]["ts"] <= t.stages[1]["ts"]


# ---------------------------------------------------------------------------
# finish
# ---------------------------------------------------------------------------

def test_finish_sets_finished_at():
    t = TraceContext()
    assert t.finished_at is None
    t.finish()
    assert t.finished_at is not None
    assert t.finished_at >= t.started_at


def test_finish_is_idempotent():
    t = TraceContext()
    t.finish()
    first = t.finished_at
    t.finish()
    assert t.finished_at == first  # not re-stamped


# ---------------------------------------------------------------------------
# elapsed_ms
# ---------------------------------------------------------------------------

def test_elapsed_ms_total_before_finish_uses_now():
    t = TraceContext()
    t.started_at = time.time() - 1.0
    e = t.elapsed_ms()
    assert e >= 1000.0


def test_elapsed_ms_total_after_finish_is_stable():
    t = TraceContext()
    t.started_at = time.time() - 0.5
    time.sleep(0.05)
    t.finish()
    e1 = t.elapsed_ms()
    time.sleep(0.1)
    e2 = t.elapsed_ms()
    # After finish, total elapsed is locked to finished_at.
    assert e1 == pytest.approx(e2, abs=0.01)


def test_elapsed_ms_per_stage_is_last_minus_first():
    t = TraceContext()
    t.record_stage("reranker", event="start")
    time.sleep(0.02)
    t.record_stage("reranker", event="finish")
    e = t.elapsed_ms("reranker")
    assert 15.0 <= e <= 200.0  # generous window for slow CI


def test_elapsed_ms_per_stage_single_event_returns_zero():
    t = TraceContext()
    t.record_stage("solo", event="start")
    assert t.elapsed_ms("solo") == 0.0


def test_elapsed_ms_missing_stage_returns_sentinel():
    t = TraceContext()
    assert t.elapsed_ms("never") == -1.0


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------

def test_to_dict_has_required_fields():
    t = TraceContext(trace_type="query")
    d = t.to_dict()
    for k in ("trace_id", "trace_type", "stages", "metadata",
              "started_at", "finished_at", "total_elapsed_ms"):
        assert k in d
    assert d["trace_type"] == "query"
    assert d["finished_at"] is None
    assert d["total_elapsed_ms"] is None


def test_to_dict_total_elapsed_ms_after_finish():
    t = TraceContext()
    t.started_at = time.time() - 0.25
    t.finish()
    d = t.to_dict()
    assert d["total_elapsed_ms"] is not None
    assert d["total_elapsed_ms"] >= 250.0


def test_to_dict_is_json_serialisable():
    t = TraceContext()
    t.record_stage("hybrid_search", event="start")
    t.finish()
    d = t.to_dict()
    # Must round-trip through json.dumps without error.
    s = json.dumps(d, default=str)
    parsed = json.loads(s)
    assert parsed["trace_id"] == d["trace_id"]
    assert parsed["trace_type"] == d["trace_type"]


# ---------------------------------------------------------------------------
# TraceCollector
# ---------------------------------------------------------------------------

def test_collector_writes_one_jsonl_line_per_trace(tmp_path: Path):
    log = tmp_path / "sub" / "traces.jsonl"
    collector = TraceCollector(log_path=log)

    t1 = new_trace(trace_type="query")
    t1.record_stage("hybrid_search", event="start")
    t1.finish()
    t2 = new_trace(trace_type="ingestion")
    t2.finish()
    collector.collect(t1)
    collector.collect(t2)

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["trace_type"] == "query"
    assert parsed[1]["trace_type"] == "ingestion"
    assert parsed[0]["trace_id"] == t1.trace_id
    assert parsed[1]["trace_id"] == t2.trace_id


def test_collector_finishes_in_flight_traces(tmp_path: Path):
    log = tmp_path / "traces.jsonl"
    t = new_trace(trace_type="query")
    # No finish() called.
    TraceCollector(log_path=log).collect(t)
    assert t.finished_at is not None
    parsed = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert parsed["total_elapsed_ms"] is not None


def test_collector_creates_parent_dirs(tmp_path: Path):
    log = tmp_path / "deep" / "nest" / "traces.jsonl"
    TraceCollector(log_path=log).collect(new_trace())
    assert log.exists()


def test_collector_default_log_path_constant():
    assert DEFAULT_LOG_PATH == "./logs/traces.jsonl"


def test_collector_collect_dict_persists_pre_serialised_payload(tmp_path: Path):
    log = tmp_path / "traces.jsonl"
    payload = {"trace_id": "abc", "trace_type": "query", "stages": []}
    TraceCollector(log_path=log).collect_dict(payload)
    line = log.read_text(encoding="utf-8").strip()
    assert json.loads(line) == payload


def test_collector_echo_logs_to_logger(tmp_path: Path, caplog):
    log = tmp_path / "traces.jsonl"
    with caplog.at_level("INFO", logger="src.core.trace.trace_context"):
        TraceCollector(log_path=log, echo=True).collect(new_trace())
    assert any("collected" in r.message for r in caplog.records)
