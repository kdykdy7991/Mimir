"""
``ApiKeyStore`` — SQLite persistence for MCP API keys.

Schema follows the access-control PRD (§4.2): one ``mcp_api_keys`` row
per key plus a ``mcp_api_key_collections`` whitelist join table.

Security posture:

- Only the SHA-256 digest of the secret is stored — never the raw key.
- Connection-per-operation with WAL (concurrent readers + single
  writer), the codebase's standard SQLite pattern
  (``web_store`` / ``file_integrity``).
- ``PRAGMA foreign_keys=ON`` so deleting a key cascades its whitelist.

Rows are exposed as :class:`KeyRecord`; the raw secret never enters
this module.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.mcp_server.auth.models import (
    DuplicateKeyNameError,
    KeyNotFoundError,
    KeyRecord,
)

DEFAULT_DB_PATH = "./data/db/mcp_access.db"
"""Default location under the project data directory."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_api_keys (
    key_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    secret_digest TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS mcp_api_key_collections (
    key_id TEXT NOT NULL,
    collection TEXT NOT NULL,
    PRIMARY KEY (key_id, collection),
    FOREIGN KEY (key_id) REFERENCES mcp_api_keys(key_id) ON DELETE CASCADE
);
"""


def _iso(dt: datetime) -> str:
    """Serialize a timestamp as UTC ISO-8601 (PRD §4.2 stores TEXT)."""
    return dt.astimezone(timezone.utc).isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class ApiKeyStore:
    """Thin SQLite store for MCP API keys and their collection whitelists."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connection / schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        # Autocommit mode (isolation_level=None): write methods manage
        # their own transactions with explicit BEGIN IMMEDIATE so
        # multi-statement writes are atomic.
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,             # wait up to 30s for the writer lock
            check_same_thread=False,  # short-lived connections, never shared
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        # Foreign keys are per-connection; enables ON DELETE CASCADE for
        # the whitelist table when a key row is removed.
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            # WAL is persistent per database file; set once at init so
            # per-request connections never contend on the pragma.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        key_id: str,
        name: str,
        secret_digest: str,
        collections: frozenset[str],
        created_at: datetime,
    ) -> None:
        """Insert a key row and its collection whitelist atomically.

        Raises :class:`DuplicateKeyNameError` if ``name`` is taken.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO mcp_api_keys "
                    "(key_id, name, secret_digest, enabled, created_at) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (key_id, name, secret_digest, _iso(created_at)),
                )
                conn.executemany(
                    "INSERT INTO mcp_api_key_collections (key_id, collection) "
                    "VALUES (?, ?)",
                    [(key_id, c) for c in collections],
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                raise DuplicateKeyNameError(
                    f"an API key named {name!r} already exists"
                ) from None
            conn.commit()
        finally:
            conn.close()

    def revoke(self, *, name: str, revoked_at: datetime) -> KeyRecord:
        """Disable the named key and stamp ``revoked_at``.

        Raises :class:`KeyNotFoundError` if no such key exists.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM mcp_api_keys WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise KeyNotFoundError(f"no API key named {name!r}")
            conn.execute(
                "UPDATE mcp_api_keys SET enabled = 0, revoked_at = ? "
                "WHERE key_id = ?",
                (_iso(revoked_at), row["key_id"]),
            )
            conn.commit()
            fresh = conn.execute(
                "SELECT * FROM mcp_api_keys WHERE key_id = ?", (row["key_id"],)
            ).fetchone()
            return self._record_from_row(conn, fresh)
        finally:
            conn.close()

    def delete(self, *, name: str) -> None:
        """Permanently remove a key and its collection whitelist."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT key_id FROM mcp_api_keys WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise KeyNotFoundError(f"no API key named {name!r}")
            conn.execute("DELETE FROM mcp_api_keys WHERE key_id = ?", (row["key_id"],))
            conn.commit()
        finally:
            conn.close()

    def rotate(
        self,
        *,
        name: str,
        key_id: str,
        secret_digest: str,
        collections: frozenset[str],
        created_at: datetime,
    ) -> KeyRecord:
        """Atomically replace the named key's credential.

        Deletes the existing row (cascading its whitelist) and inserts a
        fresh row with the same name and whitelist but a new ``key_id``
        and digest, so the previous raw key stops working immediately
        (PRD §4.3). Raises :class:`KeyNotFoundError` if ``name`` is
        unknown — the deletion and insertion happen in one transaction,
        so a race cannot leave the name missing after a successful call.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT key_id FROM mcp_api_keys WHERE name = ?", (name,)
            ).fetchone()
            if old is None:
                conn.rollback()
                raise KeyNotFoundError(f"no API key named {name!r}")
            try:
                # ON DELETE CASCADE removes the old whitelist rows.
                conn.execute(
                    "DELETE FROM mcp_api_keys WHERE key_id = ?", (old["key_id"],)
                )
                conn.execute(
                    "INSERT INTO mcp_api_keys "
                    "(key_id, name, secret_digest, enabled, created_at) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (key_id, name, secret_digest, _iso(created_at)),
                )
                conn.executemany(
                    "INSERT INTO mcp_api_key_collections (key_id, collection) "
                    "VALUES (?, ?)",
                    [(key_id, c) for c in collections],
                )
            except BaseException:
                conn.rollback()
                raise
            conn.commit()
            fresh = conn.execute(
                "SELECT * FROM mcp_api_keys WHERE key_id = ?", (key_id,)
            ).fetchone()
            return self._record_from_row(conn, fresh)
        finally:
            conn.close()

    def update_last_used(self, key_id: str, ts: datetime) -> None:
        """Best-effort ``last_used_at`` stamp (rate-limited by the caller)."""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE mcp_api_keys SET last_used_at = ? WHERE key_id = ?",
                (_iso(ts), key_id),
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_by_key_id(self, key_id: str) -> KeyRecord | None:
        """Fetch a key by its public id, or ``None`` if unknown."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM mcp_api_keys WHERE key_id = ?", (key_id,)
            ).fetchone()
            if row is None:
                return None
            return self._record_from_row(conn, row)
        finally:
            conn.close()

    def get_by_name(self, name: str) -> KeyRecord | None:
        """Fetch a key by its unique name, or ``None`` if unknown."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM mcp_api_keys WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                return None
            return self._record_from_row(conn, row)
        finally:
            conn.close()

    def list(self) -> list[KeyRecord]:
        """All keys, ordered by creation time then name."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM mcp_api_keys ORDER BY created_at, name"
            ).fetchall()
            return [self._record_from_row(conn, r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Row mapping
    # ------------------------------------------------------------------

    def _record_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> KeyRecord:
        collections = frozenset(
            r["collection"]
            for r in conn.execute(
                "SELECT collection FROM mcp_api_key_collections "
                "WHERE key_id = ?",
                (row["key_id"],),
            )
        )
        return KeyRecord(
            key_id=row["key_id"],
            name=row["name"],
            secret_digest=row["secret_digest"],
            enabled=bool(row["enabled"]),
            created_at=_parse_ts(row["created_at"]),
            revoked_at=_parse_ts(row["revoked_at"]),
            last_used_at=_parse_ts(row["last_used_at"]),
            collections=collections,
        )


__all__ = ["ApiKeyStore", "DEFAULT_DB_PATH"]
