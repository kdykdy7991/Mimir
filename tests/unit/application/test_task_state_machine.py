from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.services.task_state_machine import (
    IllegalTaskTransition,
    execution_stage_for_public,
)
from src.application.services.task_tracker import TaskRecord
from src.application.services.task_types import TaskError


def _record() -> TaskRecord:
    return TaskRecord(
        id=uuid4(), document_id=uuid4(), collection_id=uuid4(),
        source_path="a.pdf", filename="a.pdf",
    )


def test_public_progress_maps_to_durable_execution_stages() -> None:
    assert execution_stage_for_public("load") == "parse"
    assert execution_stage_for_public("split") == "chunk"
    assert execution_stage_for_public("transform") == "enrich"
    assert execution_stage_for_public("upsert") == "index"


def test_terminal_task_cannot_move_back_to_running() -> None:
    record = _record()
    record.mark_running("load")
    record.mark_succeeded()
    with pytest.raises(IllegalTaskTransition, match="succeeded -> running"):
        record.mark_running("load")


def test_stage_clock_and_failure_are_recorded() -> None:
    record = _record()
    record.mark_running("load")
    first_started = record.stage_started_at
    record.update_progress("embed", 2, 4, 50)
    assert record.execution_stage == "embed"
    assert record.stage_started_at is not None
    assert record.stage_started_at >= first_started
    record.mark_failed(TaskError(code="UPSTREAM_ERROR", message="failed"))
    assert record.status == "failed"
    assert record.finished_at is not None
