"""
File integrity checking for ingestion idempotency.

Provides:

- ``FileIntegrityChecker`` — abstract interface for any backend
  (SQLite / Redis / PostgreSQL / ...).
- ``SQLiteIntegrityChecker`` — default implementation backed by
  ``data/db/ingestion_history.db`` with WAL mode for concurrent
  readers + single writer.

The pipeline uses this layer to:

1. Compute a stable SHA256 hash of each source file.
2. Ask ``should_skip(hash, collection=...)`` before re-running the
   (potentially expensive) ingestion chain.
3. Record terminal status with ``mark_success`` / ``mark_failed``
   so subsequent runs know whether to skip or retry.

M3 multi-collection note
------------------------
The schema is keyed by ``(collection, file_hash)`` so the *same*
file content can be ingested into two different collections
independently. Legacy single-column-PK databases are migrated
in-place on first connect: existing rows are rewritten with
``collection = '_default'`` and a composite PK replaces the old one.
See :meth:`SQLiteIntegrityChecker._ensure_schema` and
``scripts/migrate_integrity_collection.py`` for the offline
equivalent.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default storage location
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = "./data/db/ingestion_history.db"

# Chunk size used when streaming large files into the hash function.
# 64 KiB is the sweet spot for SSD reads and SHA256 throughput.
_HASH_CHUNK_SIZE = 64 * 1024

# Default collection label used for rows that predate the M3 migration.
# The Web API surface still uses ``"default"`` as the boot collection
# name; this constant is the *integrity-side* marker for old data so we
# never silently lose rows during a migration.
DEFAULT_COLLECTION = "default"
_LEGACY_COLLECTION = "_default"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class FileIntegrityError(Exception):
    """Base exception for file integrity operations."""


class MissingFileError(FileIntegrityError):
    """Raised when the file under check does not exist or is unreadable.

    Named ``MissingFileError`` (not ``FileNotFoundError``) to avoid
    shadowing the stdlib builtin.
    """

    def __init__(self, path: str) -> None:
        super().__init__(f"File not found or unreadable: {path}")
        self.path = path


# ---------------------------------------------------------------------------
# Public dataclass — record returned by ``get_record`` (not required by spec
# but useful for tests / dashboard surfaces later).
# ---------------------------------------------------------------------------

@dataclass
class IngestionRecord:
    """A single row of the ingestion history table."""

    file_hash: str
    file_path: str
    file_size: int | None
    last_modified: float | None
    status: str  # "success" | "failed"
    error_msg: str | None
    created_at: float
    updated_at: float
    collection: str = DEFAULT_COLLECTION


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class FileIntegrityChecker(ABC):
    """
    Abstract interface for file-integrity / ingestion-history storage.

    Implementations are expected to be safe to share across threads
    (the pipeline may have one checker instance for the whole ingest
    job) and to persist ``mark_success`` / ``mark_failed`` calls so
    they survive process restarts.
    """

    @abstractmethod
    def compute_sha256(self, path: str) -> str:
        """
        Compute the SHA256 hex digest of the file at ``path``.

        The hash MUST be:
        - deterministic (same content → same hash across calls/processes)
        - stream-based (must work for files larger than memory)

        Raises:
            MissingFileError: if the file does not exist or is unreadable.
        """
        pass

    @abstractmethod
    def should_skip(self, file_hash: str, *, collection: str = DEFAULT_COLLECTION) -> bool:
        """
        Return True iff a previous ingestion of ``(file_hash, collection)``
        terminated with ``status='success'``.

        A previously failed ingestion MUST return False so the caller
        retries it. Per-collection scoping means the same content
        ingested into two collections is independently tracked (M3).
        """
        pass

    @abstractmethod
    def mark_success(
        self,
        file_hash: str,
        file_path: str,
        *,
        file_size: int | None = None,
        last_modified: float | None = None,
        collection: str = DEFAULT_COLLECTION,
    ) -> None:
        """Record a successful ingestion of ``file_hash`` in ``collection``."""
        pass

    @abstractmethod
    def mark_failed(
        self,
        file_hash: str,
        file_path: str,
        error_msg: str,
        *,
        file_size: int | None = None,
        last_modified: float | None = None,
        collection: str = DEFAULT_COLLECTION,
    ) -> None:
        """Record a failed ingestion attempt (retriable on next run)."""
        pass

    def get_record(
        self, file_hash: str, *, collection: str = DEFAULT_COLLECTION,
    ) -> IngestionRecord | None:
        """Look up the most recent ingestion record for a hash in a collection.

        Not required by the C2 spec — provided as a convenience for
        future dashboard / debugging surfaces. Default raises
        ``NotImplementedError`` so the SQLite implementation is the
        only one that opts in.
        """
        raise NotImplementedError

    def list_processed(
        self,
        *,
        status: str | None = None,
        collection: str | None = None,
        limit: int | None = None,
    ) -> list[IngestionRecord]:
        """
        List ingestion history records, newest first.

        ``status`` filters by ``"success"`` / ``"failed"`` if
        given; ``collection`` narrows to a single collection (None =
        every collection). ``limit`` caps the result list (None =
        no cap).

        Used by :class:`DocumentManager.list_documents` to enumerate
        known source files. Backends that can't answer this should
        raise ``NotImplementedError``.
        """
        raise NotImplementedError

    def get_record_by_path(
        self, file_path: str, *, collection: str | None = None,
    ) -> IngestionRecord | None:
        """Look up the most-recent ingestion record for a file path.

        When ``collection`` is given the search is scoped to that
        collection; ``None`` returns the latest matching path
        across every collection.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared helper — not abstract, but useful for every backend that
    # wants to use a stable streaming SHA256 implementation.
    # ------------------------------------------------------------------
    @staticmethod
    def _stream_sha256(path: str) -> str:
        """Stream the file at ``path`` through a SHA256 hasher."""
        p = Path(path)
        if not p.is_file():
            raise MissingFileError(str(p))

        h = hashlib.sha256()
        with p.open("rb") as f:
            for block in iter(lambda: f.read(_HASH_CHUNK_SIZE), b""):
                h.update(block)
        return h.hexdigest()


# ---------------------------------------------------------------------------
# SQLite default implementation
# ---------------------------------------------------------------------------

class SQLiteIntegrityChecker(FileIntegrityChecker):
    """
    Default ``FileIntegrityChecker`` backed by a single SQLite file.

    Storage layout
    --------------
    One table ``ingestion_history`` keyed by ``(collection, file_hash)``:

    - ``status`` = ``'success'``  → ``should_skip()`` returns True
    - ``status`` = ``'failed'``   → ``should_skip()`` returns False
                                    (allows the next run to retry)

    M3 migration
    ------------
    Pre-M3 databases carry a single-column PK on ``file_hash`` and no
    ``collection`` column. The first time this checker connects to
    such a database it copies the legacy rows into the new schema
    with ``collection = '_default'`` and drops the legacy table.
    The migration is idempotent (re-running on an already-migrated
    DB is a no-op).

    Concurrency
    -----------
    - WAL journal mode is enabled on connect so multiple workers can
      read while a single writer holds the lock.
    - ``check_same_thread=False`` plus a module-level ``threading.Lock``
      guard the first-connect schema setup. Each request gets its own
      short-lived connection (cheap on SQLite) so we don't share
      connections across threads.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._init_lock = threading.Lock()
        self._initialized = False

        # Make sure the parent directory exists, but defer schema
        # creation to the first call (keeps ``__init__`` cheap and
        # lets the test suite point at a tmp path before any IO).
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        """Open a new SQLite connection with sane defaults."""
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,            # wait up to 30s for the writer lock
            check_same_thread=False, # we serialize via short-lived conns
        )
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Schema + on-connect migration
    # ------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        """Create the table + indexes on first use. Idempotent.

        When an M3-era client opens a pre-M3 database, runs the
        in-place migration in the same lock so concurrent callers
        don't double-run.
        """
        with self._init_lock:
            if self._initialized:
                return
            conn = self._connect()
            try:
                # WAL mode is persistent — set once per database file.
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                # A pre-M3 table must be migrated FIRST: the v2 schema
                # creates a composite index on ``collection``, which a
                # legacy table (no such column) cannot host. Migrating
                # in place also creates the v2 table itself.
                if self._legacy_table_exists(conn):
                    self._migrate_legacy(conn)
                else:
                    self._create_v2_schema(conn)
                conn.commit()
                self._initialized = True
            finally:
                conn.close()

    def _create_v2_schema(self, conn: sqlite3.Connection) -> None:
        """Idempotent — create the M3 schema if absent."""
        # SQLite rejects parameterised DEFAULT literals; the label is
        # a module-level constant so it's safe to inline.
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS ingestion_history (
                collection    TEXT NOT NULL DEFAULT '{_LEGACY_COLLECTION}'
                    CHECK (collection <> ''),
                file_hash     TEXT NOT NULL,
                file_path     TEXT NOT NULL,
                file_size     INTEGER,
                last_modified REAL,
                status        TEXT NOT NULL CHECK (status IN ('success', 'failed')),
                error_msg     TEXT,
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL,
                PRIMARY KEY (collection, file_hash)
            )
            """,
        )
        # New per-collection status index. The legacy index
        # ``idx_ingestion_status_updated`` (on the old PK) is
        # automatically dropped by SQLite when we DROP the legacy
        # table in the migration; we don't recreate it because the
        # composite one is strictly more useful.
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ingestion_collection_status_updated
                ON ingestion_history (collection, status, updated_at DESC)
            """
        )

    @staticmethod
    def _legacy_table_exists(conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = '_legacy_ingestion_history'",
        ).fetchone()
        if row is not None:
            return True
        # A pre-M3 table has no ``collection`` column regardless of how
        # its PK was declared (inline ``file_hash TEXT PRIMARY KEY`` vs
        # table-level ``PRIMARY KEY (file_hash)``) — the column check is
        # the robust signal.
        row = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'ingestion_history'",
        ).fetchone()
        if row is None:
            return False
        cols = {
            c[1] for c in conn.execute("PRAGMA table_info(ingestion_history)")
        }
        return "collection" not in cols

    def _migrate_legacy(self, conn: sqlite3.Connection) -> None:
        """In-place migration of a pre-M3 ``ingestion_history`` table.

        Strategy:
        1. Rename current table to ``_legacy_ingestion_history``.
        2. Recreate the v2 schema.
        3. Copy legacy rows with ``collection = '_default'``.
        4. Drop the legacy table.

        The v2 schema is created earlier in the same call so the
        INSERT...SELECT has a target.
        """
        # 1. Snapshot the old table aside. ``ALTER TABLE RENAME``
        # is cheap — it just rewrites the name in sqlite_master.
        conn.execute(
            "ALTER TABLE ingestion_history "
            "RENAME TO _legacy_ingestion_history",
        )
        # 2. Create the new table on the now-empty name.
        # ``IF NOT EXISTS`` is a no-op here because we just renamed
        # the conflicting table, but it's defensive against the
        # helper being called twice.
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS ingestion_history (
                collection    TEXT NOT NULL DEFAULT '{_LEGACY_COLLECTION}'
                    CHECK (collection <> ''),
                file_hash     TEXT NOT NULL,
                file_path     TEXT NOT NULL,
                file_size     INTEGER,
                last_modified REAL,
                status        TEXT NOT NULL CHECK (status IN ('success', 'failed')),
                error_msg     TEXT,
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL,
                PRIMARY KEY (collection, file_hash)
            )
            """,
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ingestion_collection_status_updated
                ON ingestion_history (collection, status, updated_at DESC)
            """
        )
        # 3. Backfill. If the legacy table is missing one of the
        # expected columns (extremely old schema) default it so the
        # INSERT doesn't fail.
        legacy_cols = {
            row["name"] for row in
            conn.execute(
                "PRAGMA table_info(_legacy_ingestion_history)",
            ).fetchall()
        }
        select_file_hash = "file_hash" if "file_hash" in legacy_cols else "''"
        select_file_path = "file_path" if "file_path" in legacy_cols else "''"
        select_file_size = "file_size" if "file_size" in legacy_cols else "NULL"
        select_last_modified = (
            "last_modified" if "last_modified" in legacy_cols else "NULL"
        )
        select_status = "status" if "status" in legacy_cols else "'success'"
        select_error_msg = "error_msg" if "error_msg" in legacy_cols else "NULL"
        select_created_at = "created_at" if "created_at" in legacy_cols else "0.0"
        select_updated_at = "updated_at" if "updated_at" in legacy_cols else "0.0"
        conn.execute(
            f"""
            INSERT INTO ingestion_history
                (collection, file_hash, file_path, file_size, last_modified,
                 status, error_msg, created_at, updated_at)
            SELECT
                ?, {select_file_hash}, {select_file_path},
                {select_file_size}, {select_last_modified},
                {select_status}, {select_error_msg},
                {select_created_at}, {select_updated_at}
            FROM _legacy_ingestion_history
            """,
            (_LEGACY_COLLECTION,),
        )
        # 4. Drop the legacy snapshot. ``IF EXISTS`` so a re-run
        # (e.g. another client raced us) doesn't crash.
        conn.execute("DROP TABLE IF EXISTS _legacy_ingestion_history")
        logger.info(
            "migrated legacy ingestion_history → v2 schema (collection=%r)",
            _LEGACY_COLLECTION,
        )

    # ------------------------------------------------------------------
    # File hashing
    # ------------------------------------------------------------------
    def compute_sha256(self, path: str) -> str:
        # Ensure the file exists BEFORE touching the DB so a missing
        # file raises immediately, even on the first call.
        return self._stream_sha256(path)

    # ------------------------------------------------------------------
    # Skip detection
    # ------------------------------------------------------------------
    def should_skip(
        self, file_hash: str, *, collection: str = DEFAULT_COLLECTION,
    ) -> bool:
        self._ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT status FROM ingestion_history
                WHERE collection = ? AND file_hash = ?
                """,
                (collection, file_hash),
            ).fetchone()
        finally:
            conn.close()
        return row is not None and row["status"] == "success"

    # ------------------------------------------------------------------
    # Marking
    # ------------------------------------------------------------------
    def _upsert(
        self,
        file_hash: str,
        file_path: str,
        status: str,
        error_msg: str | None,
        file_size: int | None,
        last_modified: float | None,
        collection: str,
    ) -> None:
        self._ensure_schema()
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO ingestion_history
                    (collection, file_hash, file_path, file_size, last_modified,
                     status, error_msg, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection, file_hash) DO UPDATE SET
                    file_path     = excluded.file_path,
                    file_size     = excluded.file_size,
                    last_modified = excluded.last_modified,
                    status        = excluded.status,
                    error_msg     = excluded.error_msg,
                    updated_at    = excluded.updated_at
                """,
                (
                    collection,
                    file_hash,
                    file_path,
                    file_size,
                    last_modified,
                    status,
                    error_msg,
                    now,  # created_at on insert; ignored on update
                    now,  # updated_at
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_success(
        self,
        file_hash: str,
        file_path: str,
        *,
        file_size: int | None = None,
        last_modified: float | None = None,
        collection: str = DEFAULT_COLLECTION,
    ) -> None:
        self._upsert(
            file_hash=file_hash,
            file_path=file_path,
            status="success",
            error_msg=None,
            file_size=file_size,
            last_modified=last_modified,
            collection=collection,
        )

    def mark_failed(
        self,
        file_hash: str,
        file_path: str,
        error_msg: str,
        *,
        file_size: int | None = None,
        last_modified: float | None = None,
        collection: str = DEFAULT_COLLECTION,
    ) -> None:
        self._upsert(
            file_hash=file_hash,
            file_path=file_path,
            status="failed",
            error_msg=error_msg,
            file_size=file_size,
            last_modified=last_modified,
            collection=collection,
        )

    # ------------------------------------------------------------------
    # Optional convenience
    # ------------------------------------------------------------------
    def get_record(
        self, file_hash: str, *, collection: str = DEFAULT_COLLECTION,
    ) -> IngestionRecord | None:
        self._ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT file_hash, file_path, file_size, last_modified,
                       status, error_msg, created_at, updated_at, collection
                FROM ingestion_history
                WHERE collection = ? AND file_hash = ?
                """,
                (collection, file_hash),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None
        return self._row_to_record(row)

    # ------------------------------------------------------------------
    # Public utility used by ingest.py --force
    # ------------------------------------------------------------------
    def forget(
        self, file_hash: str, *, collection: str = DEFAULT_COLLECTION,
    ) -> bool:
        """Drop the row for ``(collection, file_hash)`` from the history.

        Used by the ingest script's ``--force`` flag to force
        re-ingestion of a file whose hash is already marked as success.
        Returns True iff a row was actually removed.
        """
        self._ensure_schema()
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                DELETE FROM ingestion_history
                WHERE collection = ? AND file_hash = ?
                """,
                (collection, file_hash),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Test helpers (not part of the public interface; only for tests
    # and one-off scripts that need to reset state).
    # ------------------------------------------------------------------
    def clear(self) -> None:  # pragma: no cover — test-only convenience
        """Delete all rows. Intended for tests; do not call in prod."""
        self._ensure_schema()
        conn = self._connect()
        try:
            conn.execute("DELETE FROM ingestion_history")
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # List (used by DocumentManager / dashboard)
    # ------------------------------------------------------------------
    def list_processed(
        self,
        *,
        status: str | None = None,
        collection: str | None = None,
        limit: int | None = None,
    ) -> list[IngestionRecord]:
        """SQLite-backed list — see base class for the contract."""
        self._ensure_schema()
        conn = self._connect()
        try:
            sql = (
                "SELECT file_hash, file_path, file_size, last_modified, "
                "status, error_msg, created_at, updated_at, collection "
                "FROM ingestion_history"
            )
            clauses: list[str] = []
            params: tuple = ()
            if status is not None:
                clauses.append("status = ?")
                params = params + (status,)
            if collection is not None:
                clauses.append("collection = ?")
                params = params + (collection,)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC"
            if limit is not None:
                sql += " LIMIT ?"
                params = params + (limit,)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._row_to_record(r) for r in rows]

    def get_record_by_path(
        self, file_path: str, *, collection: str | None = None,
    ) -> IngestionRecord | None:
        """SQLite-backed path lookup — see base class for contract."""
        self._ensure_schema()
        conn = self._connect()
        try:
            sql = (
                "SELECT file_hash, file_path, file_size, last_modified, "
                "status, error_msg, created_at, updated_at, collection "
                "FROM ingestion_history WHERE file_path = ?"
            )
            params: tuple = (file_path,)
            if collection is not None:
                sql += " AND collection = ?"
                params = params + (collection,)
            sql += " ORDER BY updated_at DESC LIMIT 1"
            row = conn.execute(sql, params).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_record(row)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> IngestionRecord:
        # ``collection`` is added in M3; pre-migration rows return
        # NULL in tests that exercise the migration path. Default
        # to the legacy label so callers always see a string.
        return IngestionRecord(
            file_hash=row["file_hash"],
            file_path=row["file_path"],
            file_size=row["file_size"],
            last_modified=row["last_modified"],
            status=row["status"],
            error_msg=row["error_msg"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            collection=row["collection"] or _LEGACY_COLLECTION,
        )


__all__ = [
    "DEFAULT_COLLECTION",
    "DEFAULT_DB_PATH",
    "FileIntegrityChecker",
    "FileIntegrityError",
    "IngestionRecord",
    "MissingFileError",
    "SQLiteIntegrityChecker",
]