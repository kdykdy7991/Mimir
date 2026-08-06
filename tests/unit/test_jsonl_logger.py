"""
Unit tests for the JSON Lines logger (F2).

Covers:
- JSONFormatter produces a valid JSON line with required fields
- Custom ``extra=`` kwargs are merged in at the top level
- Standard fields are NOT shadowable by extras
- Exception info is included when present
- get_trace_logger is idempotent (no duplicate handlers)
- get_trace_logger creates the parent dir
- write_trace appends one JSON line per call
- write_trace uses the same wire format as TraceCollector
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src.observability.logger import (
    JSONFormatter,
    get_trace_logger,
    write_trace,
)
from src.observability.logger import reset_collector_for_tests


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Each test gets a clean logger + collector state."""
    reset_collector_for_tests()
    # Clear any cached handlers on the default trace logger.
    log = logging.getLogger("rag.trace")
    for h in list(log.handlers):
        log.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    yield
    reset_collector_for_tests()
    log = logging.getLogger("rag.trace")
    for h in list(log.handlers):
        log.removeHandler(h)


# ---------------------------------------------------------------------------
# JSONFormatter
# ---------------------------------------------------------------------------

def _make_record(
    msg: str = "hello",
    level: int = logging.INFO,
    name: str = "rag.trace",
    extra: dict | None = None,
    exc_info=None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name, level=level, pathname=__file__,
        lineno=1, msg=msg, args=(), exc_info=exc_info,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


def test_formatter_produces_valid_json_with_required_fields():
    out = JSONFormatter().format(_make_record("hello"))
    parsed = json.loads(out)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "rag.trace"
    assert parsed["message"] == "hello"
    # ts is ISO-8601 with millisecond precision and trailing Z.
    assert parsed["ts"].endswith("Z")
    assert "T" in parsed["ts"]


def test_formatter_merges_extras_at_top_level():
    out = JSONFormatter().format(
        _make_record("trace collected", extra={
            "trace_id": "abc-123", "trace_type": "query", "n": 10,
        }),
    )
    parsed = json.loads(out)
    assert parsed["trace_id"] == "abc-123"
    assert parsed["trace_type"] == "query"
    assert parsed["n"] == 10
    # Standard fields still present.
    assert parsed["message"] == "trace collected"
    assert parsed["level"] == "INFO"


def test_formatter_does_not_let_extras_shadow_standard_fields():
    out = JSONFormatter().format(
        _make_record("real", extra={
            "level": "DEBUG",      # should be ignored
            "logger": "spoofed",   # should be ignored
            "message": "fake",     # should be ignored
            "trace_id": "real-id",
        }),
    )
    parsed = json.loads(out)
    assert parsed["level"] == "INFO"           # from record.levelname
    assert parsed["logger"] == "rag.trace"      # from record.name
    assert parsed["message"] == "real"          # from record.getMessage()
    assert parsed["trace_id"] == "real-id"      # extra is preserved


def test_formatter_ignores_private_attributes():
    out = JSONFormatter().format(
        _make_record("x", extra={"_private": "secret", "ok": "yes"}),
    )
    parsed = json.loads(out)
    assert "_private" not in parsed
    assert parsed["ok"] == "yes"


def test_formatter_includes_exception_info():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = _make_record(
            "failed", level=logging.ERROR, exc_info=sys.exc_info(),
        )
    out = JSONFormatter().format(rec)
    parsed = json.loads(out)
    assert "exc_info" in parsed
    assert "ValueError: boom" in parsed["exc_info"]


# ---------------------------------------------------------------------------
# get_trace_logger
# ---------------------------------------------------------------------------

def test_get_trace_logger_creates_file(tmp_path: Path):
    log_path = tmp_path / "sub" / "trace.jsonl"
    log = get_trace_logger(log_path=log_path)
    log.info("hello", extra={"trace_id": "x"})
    assert log_path.exists()
    line = log_path.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["message"] == "hello"
    assert parsed["trace_id"] == "x"


def test_get_trace_logger_is_idempotent(tmp_path: Path):
    log_path = tmp_path / "trace.jsonl"
    a = get_trace_logger(log_path=log_path)
    b = get_trace_logger(log_path=log_path)
    assert a is b
    # Only one FileHandler attached, not two.
    file_handlers = [
        h for h in a.handlers
        if isinstance(h, logging.FileHandler)
        and Path(h.baseFilename) == log_path
    ]
    assert len(file_handlers) == 1


def test_get_trace_logger_does_not_propagate(tmp_path: Path):
    log_path = tmp_path / "trace.jsonl"
    log = get_trace_logger(log_path=log_path)
    assert log.propagate is False


def test_get_trace_logger_creates_parent_dir(tmp_path: Path):
    log_path = tmp_path / "deep" / "nest" / "trace.jsonl"
    get_trace_logger(log_path=log_path)
    assert log_path.parent.is_dir()


# ---------------------------------------------------------------------------
# write_trace
# ---------------------------------------------------------------------------

def test_write_trace_appends_one_json_line(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "trace.jsonl"
    # Direct the singleton collector at our tmp file.
    monkeypatch.setattr(
        "src.observability.logger.TRACE_LOG_PATH", str(log_path),
    )
    reset_collector_for_tests()
    write_trace({"trace_id": "t1", "trace_type": "query", "stages": []})
    write_trace({"trace_id": "t2", "trace_type": "ingestion", "stages": []})
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["trace_id"] == "t1"
    assert json.loads(lines[1])["trace_id"] == "t2"


def test_write_trace_uses_same_wire_format_as_collector(tmp_path: Path, monkeypatch):
    """TraceCollector.collect() and write_trace() write identical lines."""
    from src.core.trace import TraceCollector, new_trace

    log_path = tmp_path / "trace.jsonl"
    monkeypatch.setattr(
        "src.observability.logger.TRACE_LOG_PATH", str(log_path),
    )
    reset_collector_for_tests()

    t = new_trace(trace_type="query")
    t.finish()
    TraceCollector(log_path=log_path).collect(t)
    write_trace(t.to_dict())

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    # Both lines have the same fields (just different trace_id).
    a = json.loads(lines[0])
    b = json.loads(lines[1])
    assert set(a.keys()) == set(b.keys())
