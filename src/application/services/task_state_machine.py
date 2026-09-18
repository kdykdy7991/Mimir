"""Durable task lifecycle rules shared by every local worker queue."""

from __future__ import annotations

from typing import Literal

ExecutionStage = Literal[
    "parse",
    "normalize",
    "chunk",
    "enrich",
    "embed",
    "index",
    "finalize",
]

TERMINAL_TASK_STATUSES = frozenset({"succeeded", "failed", "cancelled", "skipped"})

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    # A task may fail before a worker starts (invalid durable payload,
    # dispatch/claim failure), so pending -> failed is a valid terminal path.
    "pending": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled", "skipped"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "skipped": frozenset(),
}

_PUBLIC_STAGE_MAP: dict[str, ExecutionStage] = {
    "load": "parse",
    "split": "chunk",
    "transform": "enrich",
    "embed": "embed",
    "upsert": "index",
}


class IllegalTaskTransition(ValueError):
    """Raised when a worker attempts to leave a terminal/invalid state."""


def ensure_transition(current: str, target: str) -> None:
    """Validate one lifecycle transition.

    Repeating the same state is allowed so heartbeat/progress writes remain
    idempotent. Every transition out of a terminal state is rejected.
    """
    if current == target:
        return
    if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise IllegalTaskTransition(f"illegal task transition: {current} -> {target}")


def execution_stage_for_public(stage: str) -> ExecutionStage:
    """Map the backwards-compatible API progress stage to governance stage."""
    try:
        return _PUBLIC_STAGE_MAP[stage]
    except KeyError as exc:
        raise ValueError(f"unknown public task stage: {stage}") from exc


__all__ = [
    "ExecutionStage",
    "IllegalTaskTransition",
    "TERMINAL_TASK_STATUSES",
    "ensure_transition",
    "execution_stage_for_public",
]
