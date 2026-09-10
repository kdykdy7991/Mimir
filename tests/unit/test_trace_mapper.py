"""Trace wire projection edge cases."""

from src.web_api.mappers import to_trace_response


def test_ingestion_skip_event_is_not_filtered_out() -> None:
    raw = {
        "trace_id": "8e761e43-6cb6-437c-b618-42877b4c3618",
        "trace_type": "ingestion",
        "started_at": 1_789_030_798.20,
        "finished_at": 1_789_030_798.23,
        "total_elapsed_ms": 3.0,
        "stages": [{
            "name": "ingestion_pipeline",
            "ts": 1_789_030_798.22,
            "event": "skipped",
            "file_hash": "2fade1659cef",
        }],
    }

    response = to_trace_response(raw)

    assert len(response.stages) == 1
    assert response.stages[0].name == "ingestion_pipeline"
    assert response.stages[0].duration_ms == 0
    assert response.stages[0].details == {
        "event": "skipped", "file_hash": "2fade1659cef",
    }
