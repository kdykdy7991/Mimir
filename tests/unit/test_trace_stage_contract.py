"""B3.1 — trace wire contract: backward-compatible serialization, status
derivation and secret sanitization of stage/top-level error fields."""

from src.core.trace.trace_context import derive_stage_status, derive_trace_status
from src.web_api.mappers import to_trace_response


# ---------------------------------------------------------------------------
# derive_stage_status / derive_trace_status
# ---------------------------------------------------------------------------

def test_explicit_stage_status_wins():
    assert derive_stage_status({"status": "warning"}) == "warning"
    assert derive_stage_status({"status": "canceled"}) == "canceled"


def test_stage_status_derived_from_event():
    assert derive_stage_status({"event": "error"}) == "failed"
    assert derive_stage_status({"event": "skipped"}) == "skipped"
    assert derive_stage_status({"event": "cancelled"}) == "canceled"


def test_completed_stage_without_status_derives_success():
    assert derive_stage_status({"elapsed_ms": 12.3}) == "success"


def test_in_flight_stage_derives_running():
    assert derive_stage_status({"event": "start"}) == "running"


def test_missing_stage_status_never_raises():
    assert derive_stage_status({}) == "running"


def test_trace_status_explicit_wins():
    assert derive_trace_status({"status": "failed"}) == "failed"
    assert derive_trace_status({"status": "cancelled"}) == "canceled"


def test_trace_status_derived_from_stage_error_event():
    raw = {"finished_at": 1.0, "stages": [{"name": "embed", "event": "error"}]}
    assert derive_trace_status(raw) == "failed"


def test_trace_status_finished_success_default():
    assert derive_trace_status({"finished_at": 1.0}) == "success"


def test_trace_status_in_flight_running():
    assert derive_trace_status({"finished_at": None}) == "running"


# ---------------------------------------------------------------------------
# to_trace_response: old JSONL with fewer fields keeps deserialising
# ---------------------------------------------------------------------------

def _base_raw(**overrides) -> dict:
    raw = {
        "trace_id": "8e761e43-6cb6-437c-b618-42877b4c3618",
        "trace_type": "ingestion",
        "started_at": 1_789_030_798.20,
        "finished_at": 1_789_030_798.30,
        "total_elapsed_ms": 100.0,
        "stages": [{
            "name": "load", "ts": 1_789_030_798.21, "elapsed_ms": 50.0,
        }],
        "metadata": {},
    }
    raw.update(overrides)
    return raw


def test_old_jsonl_deserialises_with_defaults():
    resp = to_trace_response(_base_raw())
    assert resp.status == "success"
    assert resp.retryable is False
    assert resp.cancelable is False
    assert resp.attempt is None
    assert resp.parent_trace_id is None
    stage = resp.stages[0]
    assert stage.status == "success"
    assert stage.input_count is None
    assert stage.output_count is None
    assert stage.attempt is None
    assert stage.skip_reason is None
    assert stage.error_code is None
    assert stage.error_summary is None


def test_new_stage_fields_surface():
    raw = _base_raw(stages=[{
        "name": "embed", "ts": 1_789_030_798.22, "elapsed_ms": 50.0,
        "status": "warning", "input_count": 10, "output_count": 8,
        "attempt": 1, "skip_reason": None, "error_code": "PARTIAL",
        "error_summary": "some chunks degraded",
    }])
    stage = to_trace_response(raw).stages[0]
    assert stage.status == "warning"
    assert stage.input_count == 10
    assert stage.output_count == 8
    assert stage.attempt == 1
    assert stage.error_code == "PARTIAL"
    assert stage.error_summary == "some chunks degraded"


def test_top_level_fields_surface():
    raw = _base_raw(
        trace_id="9c2e1a40-0000-4000-8000-000000000001",
        status="failed", attempt=1,
        parent_trace_id="8e761e43-6cb6-437c-b618-42877b4c3618",
    )
    resp = to_trace_response(raw)
    assert resp.status == "failed"
    assert resp.retryable is True
    assert resp.cancelable is False
    assert resp.attempt == 1
    assert resp.parent_trace_id == "8e761e43-6cb6-437c-b618-42877b4c3618"


def test_error_fields_are_sanitized():
    secret = "Authorization: Bearer sk-0123456789abcdef"
    raw = _base_raw(
        trace_type="ingestion",
        error=f"provider failed: {secret}",
        stages=[{
            "name": "embed", "ts": 1_789_030_798.25, "elapsed_ms": 1.0,
            "event": "error", "error": f"embedding {secret} status 500",
        }],
    )
    resp = to_trace_response(raw)
    assert secret not in (resp.error or "")
    assert secret not in (resp.stages[0].error_summary or "")
    assert "sk-0123456789abcdef" not in (resp.stages[0].error_summary or "")
    # raw error is never leaked into the wire-facing details dict.
    assert "error" not in resp.stages[0].details
    # a sanitized summary is present, and the failure is credited.
    assert resp.stages[0].error_summary
    assert resp.stages[0].error_code == "STAGE_ERROR"


def test_long_error_summary_is_truncated():
    raw = _base_raw(stages=[{
        "name": "embed", "ts": 1_789_030_798.25, "elapsed_ms": 1.0,
        "event": "error", "error": "message " * 2000,
    }])
    tail = to_trace_response(raw).stages[0].error_summary
    assert tail is not None
    assert len(tail) <= 501  # 500 chars + ellipsis