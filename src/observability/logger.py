"""
Structured JSON Lines logger (F2).

Three pieces, each independently testable:

- :class:`JSONFormatter` — a :class:`logging.Formatter` that turns a
  ``LogRecord`` into a single JSON object on one line. Standard
  fields (timestamp / level / logger name / message) are always
  present; any ``extra=...`` kwargs on the log call are merged in
  alongside, so a caller can do::

      logger.info("trace collected", extra={"trace_id": "...", "n": 10})

  and the resulting line will include those keys at the top level.

- :func:`get_trace_logger` — returns a logger wired to a
  :class:`logging.FileHandler` that writes JSON Lines to
  ``./logs/trace.jsonl`` (overridable). Idempotent: re-calling
  returns the same logger with no duplicate handlers, so callers
  in tests can safely call it once per test.

- :func:`write_trace` — append a pre-serialised trace dict to the
  trace log file. Internally delegates to
  :class:`TraceCollector` so the file format stays consistent with
  the collector's wire output.

Why split ``write_trace`` from ``TraceCollector``?
- ``write_trace`` is a free function (callable from anywhere,
  including the MCP server's stdio-only environment where the
  logger is configured once at startup).
- ``TraceCollector`` is a class with state (log_path, echo flag)
  that tests instantiate directly.
Both write the same wire format.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.trace.trace_context import DEFAULT_LOG_PATH, TraceCollector

TRACE_LOGGER_NAME = "rag.trace"
TRACE_LOG_PATH = os.environ.get("RAG_TRACE_LOG", "./logs/trace.jsonl")


# ---------------------------------------------------------------------------
# JSONFormatter
# ---------------------------------------------------------------------------

# Standard ``LogRecord`` attributes we always project. Anything in
# ``record.__dict__`` that's NOT in this set (and not a private
# attribute) is treated as caller-supplied ``extra`` and merged in.
_STANDARD_LOG_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname",
    "filename", "module", "exc_info", "exc_text", "stack_info",
    "lineno", "funcName", "created", "msecs", "relativeCreated",
    "thread", "threadName", "processName", "process", "message",
    "asctime", "taskName",
})


class JSONFormatter(logging.Formatter):
    """
    Format log records as a single JSON object per line.

    The output schema::

        {
          "ts": "2026-07-23T16:30:00.123Z",  # ISO-8601 UTC
          "level": "INFO",
          "logger": "rag.trace",
          "message": "trace collected",
          ...caller-supplied extras...
        }

    Caller extras win on key collision with the standard fields,
    except for ``ts`` / ``level`` / ``logger`` / ``message`` which
    are always authoritative (so a caller can't accidentally shadow
    them and confuse downstream parsers).
    """

    def format(self, record: logging.LogRecord) -> str:
        # Build the base payload from standard fields.
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc,
            ).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z",
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Surface exception info if present.
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        # Merge caller extras (anything in __dict__ not in the
        # standard set, and not private).
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_ATTRS:
                continue
            if key.startswith("_"):
                continue
            if key in payload:
                # Don't let extras shadow the standard fields.
                continue
            payload[key] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# get_trace_logger
# ---------------------------------------------------------------------------

_attached_handlers: set[int] = set()


def get_trace_logger(
    name: str = TRACE_LOGGER_NAME,
    log_path: str | Path | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """
    Return a logger that writes JSON Lines to the trace log file.

    Idempotent: re-calling with the same arguments returns the
    same logger and does NOT add duplicate handlers. To force a
    fresh logger (e.g. in tests), pass a unique ``name``.

    The log file's parent directory is created on first call.
    """
    log_path = Path(log_path or TRACE_LOG_PATH)
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Attach a handler only if we haven't already (idempotent).
    for h in logger.handlers:
        if (
            isinstance(h, logging.FileHandler)
            and Path(h.baseFilename) == log_path
        ):
            return logger

    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(JSONFormatter())
    handler.setLevel(level)
    logger.addHandler(handler)
    # Don't propagate to the root logger — these records are for
    # the trace file, not stderr.
    logger.propagate = False
    _attached_handlers.add(id(handler))
    return logger


# ---------------------------------------------------------------------------
# write_trace
# ---------------------------------------------------------------------------

_collector: TraceCollector | None = None


def _get_default_collector() -> TraceCollector:
    """Lazy singleton: one TraceCollector per process for write_trace()."""
    global _collector
    if _collector is None:
        _collector = TraceCollector(log_path=TRACE_LOG_PATH)
    return _collector


def write_trace(trace_dict: dict[str, Any]) -> None:
    """
    Append a trace dict to the trace log file as one JSON line.

    Uses the singleton :class:`TraceCollector` so the on-disk
    format is identical to what ``TraceCollector.collect()``
    produces — readers don't need to distinguish the two sources.
    """
    _get_default_collector().collect_dict(trace_dict)


def reset_collector_for_tests() -> None:
    """Drop the singleton collector. Test-only helper."""
    global _collector
    _collector = None


__all__ = [
    "JSONFormatter",
    "TRACE_LOGGER_NAME",
    "TRACE_LOG_PATH",
    "get_trace_logger",
    "reset_collector_for_tests",
    "write_trace",
]
