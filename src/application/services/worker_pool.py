"""SQLite-backed local worker queues with recoverable leases."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

WorkerQueue = Literal["parse", "enrich", "index", "maintenance", "sync"]
WORKER_QUEUES: tuple[WorkerQueue, ...] = (
    "parse", "enrich", "index", "maintenance", "sync",
)


@dataclass(frozen=True)
class LeasedJob:
    task_id: str
    queue: WorkerQueue
    payload: dict[str, Any]
    lease_owner: str
    lease_expires_at: float
    attempt: int
    max_attempts: int


class PermanentWorkerError(Exception):
    """A deterministic payload/configuration failure that must not retry."""

    code = "permanent_failure"


class RetryableWorkerError(Exception):
    """A transient dependency failure eligible for bounded retry."""

    code = "transient_failure"


class DurableWorkerStore:
    """Minimal durable queue store using SQLite atomic write transactions."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS worker_jobs (
                    task_id          TEXT PRIMARY KEY,
                    queue            TEXT NOT NULL,
                    payload_json     TEXT NOT NULL,
                    state            TEXT NOT NULL DEFAULT 'queued',
                    available_at     REAL NOT NULL,
                    lease_owner      TEXT,
                    lease_expires_at REAL,
                    heartbeat_at     REAL,
                    attempt          INTEGER NOT NULL DEFAULT 0,
                    created_at       REAL NOT NULL,
                    updated_at       REAL NOT NULL,
                    max_attempts     INTEGER NOT NULL DEFAULT 3,
                    idempotency_key  TEXT,
                    error_class      TEXT,
                    error_code       TEXT,
                    CHECK (queue IN ('parse','enrich','index','maintenance','sync')),
                    CHECK (state IN ('queued','leased','completed','cancelled','dead_letter'))
                );
                CREATE INDEX IF NOT EXISTS idx_worker_jobs_claim
                    ON worker_jobs(queue, state, available_at, lease_expires_at, created_at);
                CREATE TABLE IF NOT EXISTS worker_job_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id     TEXT NOT NULL,
                    event       TEXT NOT NULL,
                    attempt     INTEGER NOT NULL,
                    error_class TEXT,
                    error_code  TEXT,
                    occurred_at REAL NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(worker_jobs)")}
            for name, definition in (
                ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
                ("idempotency_key", "TEXT"),
                ("error_class", "TEXT"),
                ("error_code", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE worker_jobs ADD COLUMN {name} {definition}")
            conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_jobs_idempotency
                   ON worker_jobs(queue, idempotency_key)
                   WHERE idempotency_key IS NOT NULL""",
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def enqueue(
        self, task_id: str, queue: WorkerQueue, payload: Mapping[str, Any],
        *, available_at: float | None = None, max_attempts: int = 3,
        idempotency_key: str | None = None,
    ) -> bool:
        if queue not in WORKER_QUEUES:
            raise ValueError(f"unknown worker queue: {queue}")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO worker_jobs
                   (task_id, queue, payload_json, available_at, created_at, updated_at,
                    max_attempts, idempotency_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, queue, json.dumps(dict(payload), separators=(",", ":")),
                 now if available_at is None else available_at, now, now,
                 max_attempts, idempotency_key),
            )
            if cur.rowcount == 1:
                self._event(conn, task_id, "enqueued", 0, occurred_at=now)
        return cur.rowcount == 1

    def claim(
        self, queue: WorkerQueue, owner: str, *, lease_seconds: float,
        now: float | None = None,
    ) -> LeasedJob | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        claimed_at = time.time() if now is None else now
        expires = claimed_at + lease_seconds
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM worker_jobs
                   WHERE queue = ? AND available_at <= ?
                     AND (state = 'queued' OR
                          (state = 'leased' AND lease_expires_at <= ?))
                   ORDER BY available_at, created_at, task_id LIMIT 1""",
                (queue, claimed_at, claimed_at),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            cur = conn.execute(
                """UPDATE worker_jobs SET state = 'leased', lease_owner = ?,
                       lease_expires_at = ?, heartbeat_at = ?, attempt = attempt + 1,
                       updated_at = ?
                   WHERE task_id = ? AND
                     (state = 'queued' OR (state = 'leased' AND lease_expires_at <= ?))""",
                (owner, expires, claimed_at, claimed_at, row["task_id"], claimed_at),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
            self._event(
                conn, row["task_id"], "claimed", int(row["attempt"]) + 1,
                occurred_at=claimed_at,
            )
            conn.commit()
            return LeasedJob(
                task_id=row["task_id"], queue=row["queue"],
                payload=json.loads(row["payload_json"]), lease_owner=owner,
                lease_expires_at=expires, attempt=int(row["attempt"]) + 1,
                max_attempts=int(row["max_attempts"]),
            )
        finally:
            conn.close()

    def heartbeat(
        self, task_id: str, owner: str, *, lease_seconds: float,
        now: float | None = None,
    ) -> bool:
        beat = time.time() if now is None else now
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE worker_jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                   WHERE task_id = ? AND state = 'leased' AND lease_owner = ?
                     AND lease_expires_at > ?""",
                (beat, beat + lease_seconds, beat, task_id, owner, beat),
            )
        return cur.rowcount == 1

    def complete(self, task_id: str, owner: str, *, now: float | None = None) -> bool:
        completed_at = time.time() if now is None else now
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE worker_jobs SET state = 'completed', lease_owner = NULL,
                       lease_expires_at = NULL, heartbeat_at = ?, updated_at = ?
                   WHERE task_id = ? AND state = 'leased' AND lease_owner = ?""",
                (completed_at, completed_at, task_id, owner),
            )
            if cur.rowcount == 1:
                row = conn.execute(
                    "SELECT attempt FROM worker_jobs WHERE task_id = ?", (task_id,),
                ).fetchone()
                self._event(conn, task_id, "completed", int(row["attempt"]), occurred_at=completed_at)
        return cur.rowcount == 1

    def fail(
        self, task_id: str, owner: str, *, error_class: str, error_code: str,
        retryable: bool, base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 300.0, now: float | None = None,
    ) -> str:
        """Settle a leased failure as delayed retry or dead-letter."""
        failed_at = time.time() if now is None else now
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM worker_jobs WHERE task_id = ? AND state = 'leased' AND lease_owner = ?",
                (task_id, owner),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise RuntimeError(f"worker does not own live job: {task_id}")
            exhausted = int(row["attempt"]) >= int(row["max_attempts"])
            state = "dead_letter" if exhausted or not retryable else "queued"
            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (int(row["attempt"]) - 1)))
            available_at = failed_at if state == "dead_letter" else failed_at + delay
            conn.execute(
                """UPDATE worker_jobs SET state = ?, available_at = ?, lease_owner = NULL,
                       lease_expires_at = NULL, error_class = ?, error_code = ?, updated_at = ?
                   WHERE task_id = ?""",
                (state, available_at, error_class, error_code, failed_at, task_id),
            )
            self._event(
                conn, task_id, "dead_lettered" if state == "dead_letter" else "retry_scheduled",
                int(row["attempt"]), error_class, error_code, failed_at,
            )
            conn.commit()
            return state
        finally:
            conn.close()

    def replay_dead_letter(self, task_id: str, *, now: float | None = None) -> bool:
        """Administrator replay preserving attempt/event history on the same job."""
        replayed_at = time.time() if now is None else now
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE worker_jobs SET state = 'queued', available_at = ?,
                       max_attempts = attempt + max_attempts, error_class = NULL,
                       error_code = NULL, updated_at = ?
                   WHERE task_id = ? AND state = 'dead_letter'""",
                (replayed_at, replayed_at, task_id),
            )
            if cur.rowcount == 1:
                row = conn.execute(
                    "SELECT attempt FROM worker_jobs WHERE task_id = ?", (task_id,),
                ).fetchone()
                self._event(conn, task_id, "replayed", int(row["attempt"]), occurred_at=replayed_at)
        return cur.rowcount == 1

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM worker_job_events WHERE task_id = ? ORDER BY id", (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _event(
        conn: sqlite3.Connection, task_id: str, event: str, attempt: int,
        error_class: str | None = None, error_code: str | None = None,
        occurred_at: float | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO worker_job_events
               (task_id, event, attempt, error_class, error_code, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, event, attempt, error_class, error_code,
             time.time() if occurred_at is None else occurred_at),
        )

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM worker_jobs WHERE task_id = ?", (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_dead_letters(
        self, queue: WorkerQueue, *, limit: int = 100,
    ) -> list[dict[str, Any]]:
        if queue not in WORKER_QUEUES:
            raise ValueError(f"unknown worker queue: {queue}")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM worker_jobs
                   WHERE queue=? AND state='dead_letter'
                   ORDER BY updated_at DESC,task_id LIMIT ?""",
                (queue, limit),
            ).fetchall()
        return [dict(row) for row in rows]


class WorkerContext:
    def __init__(self, store: DurableWorkerStore, job: LeasedJob, lease_seconds: float):
        self._store = store
        self.job = job
        self._lease_seconds = lease_seconds

    def heartbeat(self) -> bool:
        return self._store.heartbeat(
            self.job.task_id, self.job.lease_owner,
            lease_seconds=self._lease_seconds,
        )


WorkerHandler = Callable[[dict[str, Any], WorkerContext], None]


class DurableWorkerPool:
    """Small in-process dispatcher; durability belongs to the SQLite store."""

    def __init__(
        self, store: DurableWorkerStore, handlers: Mapping[WorkerQueue, WorkerHandler],
        *, owner: str, lease_seconds: float = 30.0,
    ) -> None:
        self.store = store
        self.handlers = dict(handlers)
        self.owner = owner
        self.lease_seconds = lease_seconds

    def run_once(self, queue: WorkerQueue) -> bool:
        handler = self.handlers.get(queue)
        if handler is None:
            return False
        job = self.store.claim(
            queue, self.owner, lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False
        try:
            handler(job.payload, WorkerContext(self.store, job, self.lease_seconds))
        except Exception as exc:  # worker boundary owns retry classification
            permanent = isinstance(exc, PermanentWorkerError)
            self.store.fail(
                job.task_id, self.owner,
                error_class="permanent" if permanent else "transient",
                error_code=str(getattr(exc, "code", "worker_failure")),
                retryable=not permanent,
            )
            return True
        if not self.store.complete(job.task_id, self.owner):
            raise RuntimeError(f"worker lost lease before completion: {job.task_id}")
        return True


__all__ = [
    "DurableWorkerPool", "DurableWorkerStore", "LeasedJob", "WorkerContext",
    "PermanentWorkerError", "RetryableWorkerError", "WorkerHandler",
    "WorkerQueue", "WORKER_QUEUES",
]
