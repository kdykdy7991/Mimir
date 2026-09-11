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

B3 additions
------------
- A lightweight SQLite index (one row per trace id) backs the new
  ``GET /traces`` list so paginated/filtered queries never scan the
  unbounded JSONL. ``record()`` upserts into it; ``list()`` reads only
  the index. ``get()`` still falls back to the JSONL scan for ids that
  predate the index (single-id lookup stays bounded).
- :meth:`upsert_live` keeps an in-memory-only snapshot of a *running*
  trace so ``GET /ingestions/{id}/trace`` can show completed stages +
  the current stage while a worker is mid-flight, without waiting for
  the final ``record()``. Merges are monotonic: a terminal stage status
  or a finished trace never regresses.

Both ``QueryService`` and ``IngestionService`` accept an optional
``trace_store`` and record a trace per query / per ingestion worker run;
the Web API reads through ``services.trace``.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.trace.trace_context import (
    derive_trace_status,
    is_stage_status_terminal,
)

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext

DEFAULT_TRACE_LOG_PATH = "./data/traces/traces.jsonl"

_TERMINAL_TRACE_STATUSES = {"success", "failed", "canceled", "skipped"}


def _merge_stages(old: list[dict], new: list[dict]) -> list[dict]:
    """Merge two stage lists by name, never regressing a terminal status.

    New stages append; an existing stage keeps its more-advanced
    (terminal) status when a fresh snapshot tries to re-report it as
    ``running``/``success``. Order follows first-seen stage name.
    """
    if not old:
        return [dict(st) for st in new]
    if not new:
        return [dict(st) for st in old]
    by_name: dict[str, dict] = {}
    for st in old:
        by_name.setdefault(st["name"], dict(st))
    for st in new:
        name = st.get("name", "")
        existing = by_name.get(name)
        if existing is None:
            by_name[name] = dict(st)
            continue
        merged = dict(st)
        if (is_stage_status_terminal(existing.get("status"))
                and existing.get("status") != st.get("status")):
            merged["status"] = existing["status"]
        if existing.get("error_summary") and not merged.get("error_summary"):
            merged["error_summary"] = existing["error_summary"]
        by_name[name] = merged
    return [by_name[k] for k in by_name]


def _collection_id(payload: dict) -> str | None:
    meta = payload.get("metadata") or {}
    raw = meta.get("collection_id")
    return str(raw) if raw is not None else None


def _document_id(payload: dict) -> str | None:
    meta = payload.get("metadata") or {}
    raw = meta.get("document_id")
    return str(raw) if raw is not None else None


def _search_text(payload: dict) -> str:
    """A small, lowercased text blob for the ``q`` filter."""
    meta = payload.get("metadata") or {}
    parts = [
        payload.get("trace_id", ""),
        payload.get("trace_type", ""),
        str(meta.get("collection") or ""),
        str(meta.get("collection_id") or ""),
        str(meta.get("document_id") or ""),
        str(meta.get("source_path") or ""),
        str(meta.get("filename") or ""),
    ]
    parts.extend(str(st.get("name", "")) for st in payload.get("stages") or [])
    return " ".join(parts).lower()


def _encode_cursor(started_at: float, trace_id: str) -> str:
    token = json.dumps({"t": started_at, "id": trace_id})
    return base64.urlsafe_b64encode(token.encode()).decode()


def _decode_cursor(cursor: str | None) -> tuple[float, str] | None:
    if not cursor:
        return None
    try:
        token = json.loads(
            base64.urlsafe_b64decode(cursor.encode()).decode(),
        )
        return float(token["t"]), str(token["id"])
    except Exception:  # noqa: BLE001 — a garbage cursor just resets.
        return None


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
        *,
        index_path: str | Path | None = None,
    ) -> None:
        self.log_path = Path(log_path)
        # Optional WebApiDB — indexed trace persistence (M3 batch 2).
        self._db = db
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        # Lightweight SQLite list index (bounded — see module docstring).
        self._index_path = (
            Path(index_path)
            if index_path
            else self.log_path.with_name(self.log_path.stem + "_index.sqlite3")
        )

    # ------------------------------------------------------------------
    # SQLite list index
    # ------------------------------------------------------------------
    def _ensure_index(self) -> sqlite3.Connection:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._index_path), timeout=10)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trace_index (
                trace_id      TEXT PRIMARY KEY,
                trace_type    TEXT,
                status        TEXT,
                collection_id TEXT,
                document_id   TEXT,
                started_at    REAL,
                finished_at   REAL,
                q_text        TEXT,
                payload_json  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trace_list
                ON trace_index(started_at DESC, trace_id DESC);
            """,
        )
        conn.commit()
        return conn

    def _upsert_index(self, conn: sqlite3.Connection, payload: dict) -> None:
        conn.execute(
            """
            INSERT INTO trace_index
                (trace_id, trace_type, status, collection_id, document_id,
                 started_at, finished_at, q_text, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trace_id) DO UPDATE SET
                trace_type=excluded.trace_type,
                status=excluded.status,
                collection_id=excluded.collection_id,
                document_id=excluded.document_id,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at,
                q_text=excluded.q_text,
                payload_json=excluded.payload_json
            """,
            (
                payload["trace_id"],
                payload.get("trace_type"),
                derive_trace_status(payload),
                _collection_id(payload),
                _document_id(payload),
                payload.get("started_at"),
                payload.get("finished_at"),
                _search_text(payload),
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def record(self, trace: "TraceContext") -> None:
        """Finish (if needed), index, and persist a trace.

        Idempotent per ``trace_id``: re-recording the same id
        overwrites the in-memory entry and appends another JSONL line
        (the last line wins for a scan — acceptable at local scale).
        Final/terminal state wins over any prior live snapshot — a stage
        status never regresses.
        """
        if trace.finished_at is None:
            trace.finish()
        payload = trace.to_dict()
        tid = payload["trace_id"]
        with self._lock:
            existing = self._records.get(tid)
            merged = self._merge_payload(existing, payload)
            self._records[tid] = merged
            if self._db is not None:
                try:
                    self._db.upsert_trace(merged)
                except Exception:  # noqa: BLE001
                    pass  # fall through to the JSONL append
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(merged, ensure_ascii=False, default=str) + "\n",
                )
            conn = self._ensure_index()
            try:
                self._upsert_index(conn, merged)
            finally:
                conn.close()

    def upsert_live(self, trace: "TraceContext") -> None:
        """Record an in-memory-only snapshot of a running trace.

        Backs ``GET /ingestions/{id}/trace`` during a worker run. The
        snapshot is merged monotonically into the in-memory index (a
        terminal stage status or a finished trace never regresses) but is
        NOT written to JSONL or the SQLite list index — those only see
        ``record()`` at completion.
        """
        payload = trace.to_dict()
        with self._lock:
            existing = self._records.get(payload["trace_id"])
            merged = self._merge_payload(existing, payload)
            self._records[payload["trace_id"]] = merged

    @staticmethod
    def _merge_payload(
        existing: dict[str, Any] | None,
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        """Final-state-wins merge between a stored and a fresh snapshot."""
        if existing is None:
            return incoming
        merged = dict(existing)
        # Top-level status: terminal never regresses.
        if (existing.get("status") in _TERMINAL_TRACE_STATUSES
                and incoming.get("status") != existing.get("status")):
            merged["status"] = existing["status"]
        else:
            merged["status"] = incoming.get("status", existing.get("status"))
        merged["stages"] = _merge_stages(
            existing.get("stages") or [], incoming.get("stages") or [],
        )
        # finished_at never clears once set.
        if existing.get("finished_at") is not None:
            merged["finished_at"] = existing["finished_at"]
        elif incoming.get("finished_at") is not None:
            merged["finished_at"] = incoming["finished_at"]
        meta = dict(existing.get("metadata") or {})
        meta.update(incoming.get("metadata") or {})
        merged["metadata"] = meta
        # attempt / parent never clear to None once known.
        merged["attempt"] = (incoming.get("attempt")
                             if incoming.get("attempt") is not None
                             else existing.get("attempt"))
        merged["parent_trace_id"] = (
            incoming.get("parent_trace_id")
            if incoming.get("parent_trace_id") is not None
            else existing.get("parent_trace_id"))
        merged["total_elapsed_ms"] = incoming.get("total_elapsed_ms")
        return merged

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

    def list(
        self,
        *,
        trace_type: str | None = None,
        status: str | None = None,
        collection_id: str | None = None,
        document_id: str | None = None,
        q: str | None = None,
        started_from: float | None = None,
        started_to: float | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Bounded, cursor-paginated trace listing (newest-first).

        Reads ONLY the SQLite index — never the unbounded JSONL — so a
        filter/query is O(effective rows) regardless of log size. Filters
        are combined with ``AND`` and pagination uses a stable keyset
        ``(started_at DESC, trace_id DESC)`` tie-break so pages never
        shift when rows are added between requests.

        Returns ``(items, next_cursor)`` where ``next_cursor`` is ``None``
        on the last page.
        """
        where: list[str] = []
        params: list[Any] = []
        if trace_type:
            where.append("trace_type = ?")
            params.append(trace_type)
        if status:
            where.append("status = ?")
            params.append(status)
        if collection_id:
            where.append("collection_id = ?")
            params.append(str(collection_id))
        if document_id:
            where.append("document_id = ?")
            params.append(str(document_id))
        if q:
            where.append("q_text LIKE ?")
            params.append(f"%{q.lower()}%")
        if started_from is not None:
            where.append("started_at >= ?")
            params.append(float(started_from))
        if started_to is not None:
            where.append("started_at <= ?")
            params.append(float(started_to))

        key = _decode_cursor(cursor)
        if key is not None:
            where.append("(started_at < ? OR (started_at = ? AND trace_id < ?))")
            params.extend([key[0], key[0], key[1]])

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = (
            "SELECT payload_json FROM trace_index "
            f"{where_sql} ORDER BY started_at DESC, trace_id DESC LIMIT ?"
        )
        params.append(limit + 1)  # +1 to detect has_more without a scan

        with self._lock:
            conn = self._ensure_index()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()

        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [json.loads(row[0]) for row in rows]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_cursor(
                float(last.get("started_at") or 0.0), last["trace_id"],
            )
        return items, next_cursor

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