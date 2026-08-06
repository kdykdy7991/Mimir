"""
TraceService (G5) — reads ``logs/traces.jsonl`` and exposes
typed accessors for the two trace pages (G5 ingestion, G6
query).

The on-disk format is a list of JSONL records, one per trace::

    {"trace_id": "...",
     "trace_type": "ingestion" | "query",
     "started_at": float,
     "finished_at": float,
     "total_elapsed_ms": float,
     "stages": {<name>: {"events": [...],
                          "elapsed_ms": float}}}

This module is read-only. Writing is the pipeline's job
(F1/F2).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


TRACE_TYPE_INGESTION = "ingestion"
TRACE_TYPE_QUERY = "query"


@dataclass
class StageEvent:
    """A single event inside a stage (start / finish / etc.)."""
    name: str
    event: str
    elapsed_ms: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Stage:
    """One stage (e.g. 'load', 'split') in a trace."""
    name: str
    elapsed_ms: float = 0.0
    events: list[StageEvent] = field(default_factory=list)


@dataclass
class TraceRecord:
    """One row in the trace history list."""
    trace_id: str
    trace_type: str
    started_at: float
    finished_at: float | None
    total_elapsed_ms: float
    stages: list[Stage] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def stage_names(self) -> list[str]:
        return [s.name for s in self.stages]


class TraceService:
    """
    Read-only service for trace JSONL.

    Usage::

        svc = TraceService("./logs/traces.jsonl")
        recent = svc.list_recent(trace_type="query", limit=20)
        for trace in recent:
            render(trace)
    """

    def __init__(self, log_path: str | Path = "./logs/traces.jsonl") -> None:
        self._log_path = Path(log_path)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def list_recent(
        self,
        *,
        trace_type: str | None = None,
        limit: int | None = None,
    ) -> list[TraceRecord]:
        """
        Newest first. ``trace_type`` filters by ``"ingestion"`` /
        ``"query"``. ``limit`` caps the result.
        """
        records = list(self._iter_records())
        if trace_type is not None:
            records = [r for r in records if r.trace_type == trace_type]
        # Newest first: descending by started_at.
        records.sort(key=lambda r: r.started_at, reverse=True)
        if limit is not None:
            records = records[:limit]
        return records

    def get(self, trace_id: str) -> TraceRecord | None:
        for rec in self._iter_records():
            if rec.trace_id == trace_id:
                return rec
        return None

    def search(
        self,
        *,
        trace_type: str | None = None,
        keyword: str | None = None,
        limit: int | None = None,
    ) -> list[TraceRecord]:
        """
        Filter by ``trace_type`` and ``keyword`` (substring
        match against any stage name or event detail string).
        """
        records = self.list_recent(trace_type=trace_type)
        if keyword:
            kw = keyword.lower()
            out: list[TraceRecord] = []
            for r in records:
                if self._record_matches_keyword(r, kw):
                    out.append(r)
            records = out
        if limit is not None:
            records = records[:limit]
        return records

    @staticmethod
    def _record_matches_keyword(record: TraceRecord, kw: str) -> bool:
        for s in record.stages:
            if kw in s.name.lower():
                return True
            for ev in s.events:
                # Search the event details as JSON — we
                # want to find user-supplied query strings
                # in the dense / sparse result details.
                if any(kw in str(v).lower() for v in ev.details.values()):
                    return True
        return False

    def _iter_records(self) -> Iterator[TraceRecord]:
        """Yield records, skipping any malformed lines."""
        if not self._log_path.is_file():
            return
        try:
            with self._log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = self._parse_line(line)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("skip malformed trace line: %s", exc)
                        continue
                    yield rec
        except OSError as exc:  # noqa: BLE001
            logger.warning("trace log read failed: %s", exc)
            return

    @staticmethod
    def _parse_line(line: str) -> TraceRecord:
        data = json.loads(line)
        return TraceRecord(
            trace_id=data.get("trace_id", ""),
            trace_type=data.get("trace_type", "unknown"),
            started_at=float(data.get("started_at", 0.0)),
            finished_at=(
                float(data["finished_at"])
                if data.get("finished_at") is not None else None
            ),
            total_elapsed_ms=float(data.get("total_elapsed_ms", 0.0)),
            stages=[
                Stage(
                    name=name,
                    elapsed_ms=float(info.get("elapsed_ms", 0.0)),
                    events=[
                        StageEvent(
                            name=name,
                            event=ev.get("event", ""),
                            elapsed_ms=(
                                float(ev["elapsed_ms"])
                                if ev.get("elapsed_ms") is not None
                                else None
                            ),
                            details={
                                k: v for k, v in ev.items()
                                if k not in ("event", "elapsed_ms")
                            },
                        )
                        for ev in info.get("events", [])
                    ],
                )
                for name, info in data.get("stages", {}).items()
            ],
            raw=data,
        )


__all__ = [
    "TraceService",
    "TraceRecord",
    "Stage",
    "StageEvent",
    "TRACE_TYPE_INGESTION",
    "TRACE_TYPE_QUERY",
]
