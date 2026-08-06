"""
``WebApiDB`` — SQLite persistence for the Web API (M3 batch 2).

Replaces the process-local task registry + JSONL trace scan with a
durable, indexed SQLite store so:

- task status survives a process restart (``GET /tasks/{id}`` no longer
  404s after a reboot);
- traces are looked up by primary key instead of a full JSONL scan;
- async query results and ``document → last query`` references live in
  queryable tables (feeding ``GET /queries/{query_id}/result`` and
  ``DocumentDetail.last_query_id``).

One database file, five tables:

- ``tasks``            — durable task records (ingestion + query)
- ``traces``           — indexed trace payloads (JSON)
- ``query_results``    — async query output (serialized QueryResult)
- ``query_citations``  — ``(query_id, document_id)`` joins for
  ``DocumentDetail.last_query_id``
- ``collections``      — persistent knowledge-base descriptions

Follows the codebase's SQLite convention (per-operation connection +
WAL) used by ``file_integrity`` / ``image_storage``. Rows are plain
dicts at this layer; the task/trace/query services translate their
domain objects to/from dicts.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = "./data/db/web_api.db"
"""Default location under ``data_dir`` — composition passes the real path."""


class WebApiDB:
    """Thin SQLite persistence for tasks / traces / query results."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id        TEXT PRIMARY KEY,
                    task_type      TEXT NOT NULL DEFAULT 'ingestion',
                    document_id    TEXT,
                    collection_id  TEXT,
                    source_path    TEXT NOT NULL DEFAULT '',
                    filename       TEXT NOT NULL DEFAULT '',
                    status         TEXT NOT NULL,
                    progress_json  TEXT,
                    error_json     TEXT,
                    attempt        INTEGER NOT NULL DEFAULT 0,
                    created_at     REAL NOT NULL,
                    updated_at     REAL NOT NULL,
                    finished_at    REAL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_doc
                    ON tasks(collection_id, source_path, created_at DESC);

                CREATE TABLE IF NOT EXISTS traces (
                    trace_id     TEXT PRIMARY KEY,
                    trace_type   TEXT,
                    payload_json TEXT NOT NULL,
                    created_at   REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS query_results (
                    query_id    TEXT PRIMARY KEY,
                    collection  TEXT NOT NULL,
                    query_text  TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS query_citations (
                    query_id    TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    PRIMARY KEY (query_id, document_id)
                );
                CREATE INDEX IF NOT EXISTS idx_citations_doc
                    ON query_citations(document_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS collections (
                    name        TEXT PRIMARY KEY,
                    description TEXT,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # WAL: concurrent readers + a single writer — the pattern the
        # rest of the codebase uses for its SQLite stores.
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------
    def upsert_collection(
        self, name: str, description: str | None,
    ) -> None:
        """Persist knowledge-base metadata independently of its indexes."""
        import time

        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO collections (name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description,
                    updated_at  = excluded.updated_at
                """,
                (name, description, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def get_collection(self, name: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM collections WHERE name = ?", (name,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def delete_collection(self, name: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM collections WHERE name = ?", (name,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------
    def upsert_task(self, row: dict[str, Any]) -> None:
        """Insert or replace a task row."""
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO tasks
                    (task_id, task_type, document_id, collection_id,
                     source_path, filename, status, progress_json,
                     error_json, attempt, created_at, updated_at,
                     finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    task_type     = excluded.task_type,
                    document_id   = excluded.document_id,
                    collection_id = excluded.collection_id,
                    source_path   = excluded.source_path,
                    filename      = excluded.filename,
                    status        = excluded.status,
                    progress_json = excluded.progress_json,
                    error_json    = excluded.error_json,
                    attempt       = excluded.attempt,
                    updated_at    = excluded.updated_at,
                    finished_at   = excluded.finished_at
                """,
                (
                    row["task_id"], row.get("task_type", "ingestion"),
                    row.get("document_id"), row.get("collection_id"),
                    row.get("source_path", ""), row.get("filename", ""),
                    row["status"], row.get("progress_json"),
                    row.get("error_json"), row.get("attempt", 0),
                    row.get("created_at", 0.0), row.get("updated_at", 0.0),
                    row.get("finished_at"),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def latest_task_for(
        self, collection_id: str, source_path: str,
    ) -> dict[str, Any] | None:
        """Most recent task row for a ``(collection_id, source_path)``."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE collection_id = ? AND source_path = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (collection_id, source_path),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # Traces
    # ------------------------------------------------------------------
    def upsert_trace(self, payload: dict[str, Any]) -> None:
        """Insert or replace a trace payload (keyed by ``trace_id``)."""
        tid = payload.get("trace_id", "")
        if not tid:
            return
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO traces (trace_id, trace_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    trace_type   = excluded.trace_type,
                    payload_json = excluded.payload_json,
                    created_at   = excluded.created_at
                """,
                (
                    tid, payload.get("trace_type"),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    payload.get("started_at", 0.0),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT payload_json FROM traces WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return json.loads(row["payload_json"])

    # ------------------------------------------------------------------
    # Query results + document references
    # ------------------------------------------------------------------
    def save_query_result(
        self,
        *,
        query_id: str,
        collection: str,
        query_text: str,
        result_json: str,
        document_ids: list[str],
        created_at: float | None = None,
    ) -> None:
        """Persist an async query's output + the documents it cited."""
        import time

        created = created_at if created_at is not None else time.time()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO query_results
                    (query_id, collection, query_text, result_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(query_id) DO UPDATE SET
                    collection  = excluded.collection,
                    query_text  = excluded.query_text,
                    result_json = excluded.result_json,
                    created_at  = excluded.created_at
                """,
                (query_id, collection, query_text, result_json, created),
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO query_citations
                    (query_id, document_id, created_at)
                VALUES (?, ?, ?)
                """,
                [(query_id, doc_id, created) for doc_id in document_ids],
            )
            conn.commit()
        finally:
            conn.close()

    def get_query_result(self, query_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM query_results WHERE query_id = ?",
                (query_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def last_query_for_document(self, document_id: str) -> str | None:
        """Most recent ``query_id`` that cited ``document_id`` (or None)."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT query_id FROM query_citations
                WHERE document_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (document_id,),
            ).fetchone()
        finally:
            conn.close()
        return row["query_id"] if row is not None else None

    # ------------------------------------------------------------------
    # Overview metrics
    # ------------------------------------------------------------------
    def list_query_results_between(
        self, start_at: float, end_at: float,
    ) -> list[dict[str, Any]]:
        """Query result rows in a half-open reporting interval."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT query_id, collection, query_text, result_json, created_at
                FROM query_results
                WHERE created_at >= ? AND created_at < ?
                ORDER BY created_at ASC
                """,
                (start_at, end_at),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def list_tasks_between(
        self, start_at: float, end_at: float, *, task_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Task rows in a half-open reporting interval."""
        conn = self._connect()
        try:
            if task_type is None:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE created_at >= ? AND created_at < ? ORDER BY created_at ASC",
                    (start_at, end_at),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE created_at >= ? AND created_at < ? AND task_type = ? ORDER BY created_at ASC",
                    (start_at, end_at, task_type),
                ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def ping(self) -> None:
        """Liveness probe — raises if the SQLite file is unreadable."""
        conn = self._connect()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()


__all__ = ["DEFAULT_DB_PATH", "WebApiDB"]
