"""SQLite repository for immutable chunk revisions and active pointers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.application.contracts.revisions import (
    ChunkRevision,
    RevisionSource,
    RevisionStatus,
    legacy_revision_id,
    revision_checksum,
)


class RevisionStoreError(RuntimeError):
    pass


class RevisionConflictError(RevisionStoreError):
    pass


class RevisionStore:
    """Append-only revision payloads with transactional lifecycle state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS chunk_revisions (
                    revision_id TEXT PRIMARY KEY,
                    collection TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    base_revision_id TEXT,
                    source TEXT NOT NULL CHECK(source IN ('initial','edit','rollback')),
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    checksum_sha256 TEXT NOT NULL,
                    rollback_target_revision_id TEXT,
                    FOREIGN KEY(base_revision_id) REFERENCES chunk_revisions(revision_id),
                    FOREIGN KEY(rollback_target_revision_id) REFERENCES chunk_revisions(revision_id)
                );
                CREATE INDEX IF NOT EXISTS ix_chunk_revision_history
                    ON chunk_revisions(collection, document_id, chunk_id, created_at, revision_id);
                CREATE TABLE IF NOT EXISTS chunk_revision_state (
                    revision_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('pending_index','active','retired','index_failed')),
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(revision_id) REFERENCES chunk_revisions(revision_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_chunk_revision
                    ON chunk_revision_state(revision_id) WHERE status='active';
                CREATE TABLE IF NOT EXISTS active_chunk_revisions (
                    collection TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(collection, document_id, chunk_id),
                    FOREIGN KEY(revision_id) REFERENCES chunk_revisions(revision_id)
                );
            """)

    def ensure_initial(
        self, *, collection: str, document_id: str, chunk_id: str,
        text: str, metadata: dict | None = None,
    ) -> ChunkRevision:
        checksum = revision_checksum(text, metadata)
        revision = ChunkRevision(
            revision_id=legacy_revision_id(collection, document_id, chunk_id, checksum),
            collection=collection, document_id=document_id, chunk_id=chunk_id,
            text=text, metadata=metadata or {}, source=RevisionSource.INITIAL,
            reason="legacy initial snapshot", actor="system:migration",
            checksum_sha256=checksum,
        )
        with self._connect() as conn:
            active = conn.execute(
                "SELECT revision_id FROM active_chunk_revisions WHERE collection=? AND document_id=? AND chunk_id=?",
                (collection, document_id, chunk_id),
            ).fetchone()
            if active:
                return self.get(str(active["revision_id"]), connection=conn)
            self._insert(conn, revision, RevisionStatus.ACTIVE)
            conn.execute(
                "INSERT INTO active_chunk_revisions VALUES (?,?,?,?)",
                (collection, document_id, chunk_id, revision.revision_id),
            )
        return revision

    def add_pending(self, revision: ChunkRevision) -> None:
        if revision.source is RevisionSource.INITIAL:
            raise RevisionStoreError("initial revisions must use ensure_initial")
        with self._connect() as conn:
            active = conn.execute(
                "SELECT revision_id FROM active_chunk_revisions WHERE collection=? AND document_id=? AND chunk_id=?",
                (revision.collection, revision.document_id, revision.chunk_id),
            ).fetchone()
            current = str(active["revision_id"]) if active else None
            if current != revision.base_revision_id:
                raise RevisionConflictError("base revision is not current")
            if revision.rollback_target_revision_id:
                target = self.get(
                    revision.rollback_target_revision_id, connection=conn,
                )
                if (
                    target.collection, target.document_id, target.chunk_id,
                ) != (
                    revision.collection, revision.document_id, revision.chunk_id,
                ):
                    raise RevisionStoreError(
                        "rollback target belongs to a different chunk",
                    )
            self._insert(conn, revision, RevisionStatus.PENDING_INDEX)

    def activate(self, revision_id: str) -> None:
        with self._connect() as conn:
            revision = self.get(revision_id, connection=conn)
            state = self.status(revision_id, connection=conn)
            if state not in {RevisionStatus.PENDING_INDEX, RevisionStatus.INDEX_FAILED}:
                raise RevisionStoreError("revision is not pending activation")
            active = conn.execute(
                "SELECT revision_id FROM active_chunk_revisions WHERE collection=? AND document_id=? AND chunk_id=?",
                (revision.collection, revision.document_id, revision.chunk_id),
            ).fetchone()
            current = str(active["revision_id"]) if active else None
            if current != revision.base_revision_id:
                raise RevisionConflictError("base revision is no longer current")
            now = datetime.now(timezone.utc).isoformat()
            if current:
                conn.execute(
                    "UPDATE chunk_revision_state SET status='retired', updated_at=? WHERE revision_id=?",
                    (now, current),
                )
            conn.execute(
                "INSERT OR REPLACE INTO active_chunk_revisions VALUES (?,?,?,?)",
                (revision.collection, revision.document_id, revision.chunk_id, revision_id),
            )
            conn.execute(
                "UPDATE chunk_revision_state SET status='active', updated_at=? WHERE revision_id=?",
                (now, revision_id),
            )

    def mark_index_failed(self, revision_id: str) -> None:
        with self._connect() as conn:
            if self.status(revision_id, connection=conn) not in {
                RevisionStatus.PENDING_INDEX, RevisionStatus.INDEX_FAILED,
            }:
                raise RevisionStoreError("only pending or failed revisions can fail indexing")
            conn.execute(
                "UPDATE chunk_revision_state SET status='index_failed', updated_at=? WHERE revision_id=?",
                (datetime.now(timezone.utc).isoformat(), revision_id),
            )

    def active(self, collection: str, document_id: str, chunk_id: str) -> ChunkRevision | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT revision_id FROM active_chunk_revisions WHERE collection=? AND document_id=? AND chunk_id=?",
                (collection, document_id, chunk_id),
            ).fetchone()
            return self.get(str(row["revision_id"]), connection=conn) if row else None

    def active_for_document_chunk(
        self, document_id: str, chunk_id: str,
    ) -> ChunkRevision | None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT revision_id FROM active_chunk_revisions WHERE document_id=? AND chunk_id=?",
                (document_id, chunk_id),
            ).fetchall()
            if len(rows) > 1:
                raise RevisionStoreError("ambiguous document/chunk identity")
            return self.get(str(rows[0]["revision_id"]), connection=conn) if rows else None

    def history(self, collection: str, document_id: str, chunk_id: str) -> list[tuple[ChunkRevision, RevisionStatus]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT r.*, s.status FROM chunk_revisions r
                JOIN chunk_revision_state s USING(revision_id)
                WHERE collection=? AND document_id=? AND chunk_id=?
                ORDER BY created_at, revision_id
            """, (collection, document_id, chunk_id)).fetchall()
        return [(self._from_row(row), RevisionStatus(row["status"])) for row in rows]

    def get(self, revision_id: str, *, connection: sqlite3.Connection | None = None) -> ChunkRevision:
        if connection is None:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM chunk_revisions WHERE revision_id=?", (revision_id,)).fetchone()
        else:
            row = connection.execute("SELECT * FROM chunk_revisions WHERE revision_id=?", (revision_id,)).fetchone()
        if row is None:
            raise RevisionStoreError("revision not found")
        return self._from_row(row)

    def status(self, revision_id: str, *, connection: sqlite3.Connection | None = None) -> RevisionStatus:
        if connection is None:
            with self._connect() as conn:
                row = conn.execute("SELECT status FROM chunk_revision_state WHERE revision_id=?", (revision_id,)).fetchone()
        else:
            row = connection.execute("SELECT status FROM chunk_revision_state WHERE revision_id=?", (revision_id,)).fetchone()
        if row is None:
            raise RevisionStoreError("revision state not found")
        return RevisionStatus(row["status"])

    @staticmethod
    def _insert(conn: sqlite3.Connection, revision: ChunkRevision, status: RevisionStatus) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO chunk_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                revision.revision_id, revision.collection, revision.document_id,
                revision.chunk_id, revision.base_revision_id, revision.source.value,
                revision.reason, revision.actor, revision.created_at.isoformat(),
                revision.text, json.dumps(revision.metadata, ensure_ascii=False, sort_keys=True),
                revision.checksum_sha256, revision.rollback_target_revision_id,
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO chunk_revision_state VALUES (?,?,?)",
            (revision.revision_id, status.value, revision.created_at.isoformat()),
        )
        stored = conn.execute(
            "SELECT collection, document_id, chunk_id, checksum_sha256 FROM chunk_revisions WHERE revision_id=?",
            (revision.revision_id,),
        ).fetchone()
        expected = (
            revision.collection, revision.document_id, revision.chunk_id,
            revision.checksum_sha256,
        )
        actual = tuple(stored) if stored is not None else ()
        if actual != expected:
            raise RevisionStoreError("revision_id already belongs to different content")

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ChunkRevision:
        return ChunkRevision(
            revision_id=str(row["revision_id"]), collection=str(row["collection"]),
            document_id=str(row["document_id"]), chunk_id=str(row["chunk_id"]),
            base_revision_id=row["base_revision_id"], source=RevisionSource(row["source"]),
            reason=str(row["reason"]), actor=str(row["actor"]),
            created_at=datetime.fromisoformat(str(row["created_at"])), text=str(row["text"]),
            metadata=json.loads(row["metadata_json"]), checksum_sha256=str(row["checksum_sha256"]),
            rollback_target_revision_id=row["rollback_target_revision_id"],
        )


__all__ = ["RevisionConflictError", "RevisionStore", "RevisionStoreError"]
