"""Append-only, security-safe audit ledger with centralized redaction."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

_FORBIDDEN_KEYS = frozenset({
    "authorization", "cookie", "headers", "secret", "api_key", "token",
    "query", "evidence", "content", "source_path", "path",
})


def sanitize_audit_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return bounded metadata with sensitive fields removed recursively."""
    def clean(item: Any, depth: int = 0) -> Any:
        if depth > 4:
            return "[depth-limited]"
        if isinstance(item, Mapping):
            return {
                str(key): clean(val, depth + 1)
                for key, val in item.items()
                if str(key).casefold() not in _FORBIDDEN_KEYS
            }
        if isinstance(item, (list, tuple)):
            return [clean(part, depth + 1) for part in item[:50]]
        if isinstance(item, str):
            return item[:500]
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return type(item).__name__

    return clean(dict(value or {}))


class AuditStore:
    """SQLite append-only ledger; no update/delete API is exposed."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence       INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id       TEXT NOT NULL UNIQUE,
                    occurred_at    REAL NOT NULL,
                    actor_type     TEXT NOT NULL,
                    actor_id       TEXT NOT NULL,
                    action         TEXT NOT NULL,
                    resource_type  TEXT NOT NULL,
                    resource_id    TEXT,
                    collection_id  TEXT,
                    outcome        TEXT NOT NULL,
                    request_id     TEXT,
                    metadata_json  TEXT NOT NULL,
                    previous_hash  TEXT,
                    event_hash     TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_type, actor_id, occurred_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def append(
        self, *, actor_type: str, actor_id: str, action: str,
        resource_type: str, outcome: str, resource_id: str | None = None,
        collection_id: str | None = None, request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None, occurred_at: float | None = None,
    ) -> str:
        event_id = str(uuid4())
        timestamp = time.time() if occurred_at is None else occurred_at
        safe = sanitize_audit_metadata(metadata)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1",
            ).fetchone()
            previous_hash = str(previous["event_hash"]) if previous else None
            payload = {
                "event_id": event_id, "occurred_at": timestamp,
                "actor_type": actor_type, "actor_id": actor_id,
                "action": action, "resource_type": resource_type,
                "resource_id": resource_id, "collection_id": collection_id,
                "outcome": outcome, "request_id": request_id,
                "metadata": safe, "previous_hash": previous_hash,
            }
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            event_hash = hashlib.sha256(encoded.encode()).hexdigest()
            conn.execute(
                """INSERT INTO audit_events
                   (event_id, occurred_at, actor_type, actor_id, action,
                    resource_type, resource_id, collection_id, outcome,
                    request_id, metadata_json, previous_hash, event_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, timestamp, actor_type, actor_id, action,
                 resource_type, resource_id, collection_id, outcome,
                 request_id, json.dumps(safe, ensure_ascii=False, sort_keys=True),
                 previous_hash, event_hash),
            )
            conn.commit()
            return event_id
        finally:
            conn.close()

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events ORDER BY sequence DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
