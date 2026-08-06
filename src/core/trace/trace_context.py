"""
Trace context and collector (F1).

Two pieces:

- :class:`TraceContext` — captures the timeline of a single
  ingestion or query run as a sequence of named events. Each
  ``record_stage(name, ...)`` call appends an event with a
  timestamp; the trace itself tracks ``started_at`` / ``finished_at``
  for total-duration reporting.

- :class:`TraceCollector` — accepts a finished ``TraceContext`` and
  persists it. The default backend writes one JSON line per trace to
  ``logs/traces.jsonl`` (overridable via constructor / settings).
  This separation lets us swap the backend in F2 (JSON logger) and
  later in G (Streamlit dashboard) without touching call sites.

Why dataclass + sentinel ``None`` for ``finished_at``?
- ``to_dict()`` must be JSON-serialisable, and we want to surface
  in-flight traces (e.g. a long-running ingestion that crashed).
- A sentinel ``None`` distinguishes "not finished" from "finished
  in 0 ms" (which is a real, rare but possible value).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Supported trace types — kept as constants for typo-proofing.
TRACE_TYPE_INGESTION = "ingestion"
TRACE_TYPE_QUERY = "query"

VALID_TRACE_TYPES = (TRACE_TYPE_INGESTION, TRACE_TYPE_QUERY)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TraceContext
# ---------------------------------------------------------------------------

@dataclass
class TraceContext:
    """
    A single end-to-end trace.

    Attributes:
        trace_id: UUID4 string, unique per ingestion/query run.
        trace_type: "ingestion" or "query" — disambiguates usage
            when the same trace store is shared by both pipelines.
        stages: Ordered list of stage events (append-only).
        metadata: Free-form side data (e.g. file path, model name).
        started_at: Wall-clock seconds when the trace began.
        finished_at: Wall-clock seconds when ``finish()`` was called,
            or ``None`` if still in flight.
    """

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_type: str = TRACE_TYPE_INGESTION
    stages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        if self.trace_type not in VALID_TRACE_TYPES:
            raise ValueError(
                f"trace_type must be one of {VALID_TRACE_TYPES!r}, "
                f"got {self.trace_type!r}",
            )

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------
    def record_stage(self, name: str, **data: Any) -> None:
        """Append a stage event with a timestamp + arbitrary key-value data."""
        event = {"name": name, "ts": time.time(), **data}
        self.stages.append(event)

    def finish(self) -> None:
        """Mark the trace complete. Idempotent."""
        if self.finished_at is None:
            self.finished_at = time.time()

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------
    def elapsed_ms(self, stage_name: str | None = None) -> float:
        """
        Return elapsed wall-clock time in milliseconds.

        With no argument: total time from ``started_at`` to
        ``finished_at`` (or now, if the trace is still in flight).

        With a ``stage_name``: time spanned by events with that
        name (``last.ts - first.ts``). Zero if there is only one
        matching event. ``-1.0`` if the stage name isn't found —
        matches the "stage not present" sentinel we'd want for
        assertion-style tests.

        Each "stage" is conventionally bracketed by two
        ``record_stage(name, event="start"|"finish")`` calls, so
        the duration reflects the actual work between them.
        """
        if stage_name is None:
            end = self.finished_at if self.finished_at is not None else time.time()
            return max(0.0, (end - self.started_at) * 1000.0)
        matching = [e["ts"] for e in self.stages if e.get("name") == stage_name]
        if not matching:
            return -1.0
        if len(matching) == 1:
            return 0.0
        return max(0.0, (matching[-1] - matching[0]) * 1000.0)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """
        JSON-serialisable view of the trace.

        Includes a derived ``total_elapsed_ms`` field (``None`` if
        the trace has not been finished yet) so downstream readers
        don't need to compute it themselves.
        """
        total_ms: float | None = None
        if self.finished_at is not None:
            total_ms = max(0.0, (self.finished_at - self.started_at) * 1000.0)
        out: dict[str, Any] = {
            "trace_id": self.trace_id,
            "trace_type": self.trace_type,
            "stages": list(self.stages),
            "metadata": dict(self.metadata),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_elapsed_ms": total_ms,
        }
        return out


def new_trace(trace_type: str = TRACE_TYPE_INGESTION) -> TraceContext:
    """Convenience constructor for the common case."""
    return TraceContext(trace_type=trace_type)


# ---------------------------------------------------------------------------
# TraceCollector
# ---------------------------------------------------------------------------

DEFAULT_LOG_PATH = "./logs/traces.jsonl"


class TraceCollector:
    """
    Accepts finished ``TraceContext`` instances and persists them.

    Default behaviour: append one JSON object per trace to
    ``logs/traces.jsonl``. The path is configurable so tests can
    point at a tmp dir, and so a future observability stack can
    route traces to a different sink without changing call sites.

    The collector is intentionally simple — no batching, no async,
    no rotation. Those concerns belong to a future observability
    stack (see Phase G for the dashboard reader).
    """

    def __init__(
        self,
        log_path: str | Path = DEFAULT_LOG_PATH,
        *,
        echo: bool = False,
    ) -> None:
        """
        Args:
            log_path: File to append JSONL records to. Created with
                parents if it doesn't exist on the first ``collect``.
            echo: If True, also emit the trace to the ``trace``
                logger at INFO level (useful in dev / tests).
        """
        self.log_path = Path(log_path)
        self.echo = echo

    def collect(self, trace: TraceContext) -> None:
        """
        Persist a trace. If the trace hasn't been ``finish()``-ed,
        we finish it here — better to record a slightly-incomplete
        trace than to lose it because a caller forgot to call
        ``finish()``.
        """
        if trace.finished_at is None:
            trace.finish()
        payload = trace.to_dict()
        line = json.dumps(payload, ensure_ascii=False, default=str)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.echo:
            logger.info("trace %s collected", trace.trace_id)

    def collect_dict(self, payload: dict[str, Any]) -> None:
        """
        Persist a pre-serialised dict (e.g. when the trace came from
        a different process). Same wire format as ``collect``.
        """
        line = json.dumps(payload, ensure_ascii=False, default=str)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


__all__ = [
    "DEFAULT_LOG_PATH",
    "TRACE_TYPE_INGESTION",
    "TRACE_TYPE_QUERY",
    "TraceCollector",
    "TraceContext",
    "VALID_TRACE_TYPES",
    "new_trace",
]
