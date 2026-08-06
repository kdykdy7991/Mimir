"""
``TraceStore`` — in-memory trace index + JSONL persistence (v0.2).

M2 batch 3 gives the Web API two real trace endpoints
(``GET /queries/{id}/trace`` and ``GET /ingestions/{id}/trace``). To
serve them we need a *lookup-by-id* store — ``TraceCollector`` only
appends JSONL and the dashboard's ``TraceService`` full-scans it.

Design for local scale (per v0.2 decision, see
``docs/plan-2026-07-31-m2-batch3.md``):

- Primary index: an in-memory ``dict[trace_id -> to_dict()]`` — fast,
  survives for the life of the application services.
- Durability: every ``record()`` also appends one JSON line to
  ``{data_dir}/traces/traces.jsonl``. ``get()`` falls back to scanning
  that file when the id isn't in memory, so a process restart doesn't
  lose the early traces (matching the "重启后早期 trace 仍可查" goal).

Both ``QueryService`` and ``IngestionService`` accept an optional
``trace_store`` and record a trace per query / per ingestion worker run;
the Web API reads through ``services.trace``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext

DEFAULT_TRACE_LOG_PATH = "./data/traces/traces.jsonl"


class TraceStore:
    """Record finished traces and look them up by id.

    Thread-safe: ``record`` / ``get`` are guarded by a single lock.
    The JSONL scan on a miss is best-effort (a partially-appended line
    is skipped and retried on the next call).
    """

    def __init__(
        self,
        log_path: str | Path = DEFAULT_TRACE_LOG_PATH,
        db=None,
    ) -> None:
        self.log_path = Path(log_path)
        # Optional WebApiDB — indexed trace persistence (M3 batch 2).
        self._db = db
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def record(self, trace: "TraceContext") -> None:
        """Finish (if needed), index, and persist a trace.

        Idempotent per ``trace_id``: re-recording the same id
        overwrites the in-memory entry and appends another JSONL line
        (the last line wins for a scan — acceptable at local scale).
        """
        if trace.finished_at is None:
            trace.finish()
        payload = trace.to_dict()
        tid = payload["trace_id"]
        with self._lock:
            self._records[tid] = payload
            # Durable index (SQLite) when wired; JSONL stays for
            # backward compat with the v0.2 layout.
            if self._db is not None:
                try:
                    self._db.upsert_trace(payload)
                except Exception:  # noqa: BLE001
                    pass  # fall through to the JSONL append
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(payload, ensure_ascii=False, default=str)
                    + "\n",
                )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get(self, trace_id: str) -> dict[str, Any] | None:
        """Return the trace dict for ``trace_id`` or ``None``.

        The returned dict is a copy — callers may mutate it freely
        without corrupting the index.
        """
        with self._lock:
            hit = self._records.get(trace_id)
        if hit is not None:
            return dict(hit)
        # Durable store lookup first (indexed), then the legacy JSONL scan.
        if self._db is not None:
            stored = self._db.get_trace(trace_id)
            if stored is not None:
                return dict(stored)
        return self._scan(trace_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _scan(self, trace_id: str) -> dict[str, Any] | None:
        """Full-scan the JSONL for a trace id (restart recovery)."""
        if not self.log_path.is_file():
            return None
        try:
            with self.log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    if data.get("trace_id") == trace_id:
                        return data
        except OSError:
            return None
        return None


__all__ = ["DEFAULT_TRACE_LOG_PATH", "TraceStore"]
