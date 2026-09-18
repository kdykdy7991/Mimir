"""Persist remote-vs-manual conflicts without overwriting active knowledge."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

from src.connectors.contracts import SourceDocument


class SyncConflictStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path); self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sync_bindings (
                    source_id TEXT NOT NULL, external_id TEXT NOT NULL,
                    document_id TEXT NOT NULL, last_synced_revision_id TEXT,
                    PRIMARY KEY(source_id, external_id));
                CREATE TABLE IF NOT EXISTS sync_conflicts (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, external_id TEXT NOT NULL,
                    document_id TEXT NOT NULL, active_revision_id TEXT NOT NULL,
                    last_synced_revision_id TEXT, remote_revision TEXT NOT NULL,
                    remote_metadata_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    resolved_at REAL,
                    resolution TEXT);
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sync_conflicts)")}
            if "resolved_at" not in columns:
                conn.execute("ALTER TABLE sync_conflicts ADD COLUMN resolved_at REAL")
            if "resolution" not in columns:
                conn.execute("ALTER TABLE sync_conflicts ADD COLUMN resolution TEXT")

    def _connect(self):
        conn=sqlite3.connect(str(self.db_path)); conn.row_factory=sqlite3.Row; return conn

    def bind(self, source_id: str, external_id: str, document_id: str, revision_id: str) -> None:
        with self._connect() as conn:
            conn.execute("""INSERT INTO sync_bindings VALUES (?,?,?,?) ON CONFLICT(source_id,external_id)
                DO UPDATE SET document_id=excluded.document_id,last_synced_revision_id=excluded.last_synced_revision_id""",
                (source_id,external_id,document_id,revision_id))

    def binding(self, source_id: str, external_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_bindings WHERE source_id=? AND external_id=?",
                (source_id, external_id),
            ).fetchone()
        return dict(row) if row else None

    def apply_remote(
        self, *, source_id: str, document_id: str, active_revision_id: str,
        document: SourceDocument, create_revision: Callable[[SourceDocument], str],
    ) -> tuple[str, str]:
        with self._connect() as conn:
            binding=conn.execute("SELECT * FROM sync_bindings WHERE source_id=? AND external_id=?",
                (source_id,document.external_id)).fetchone()
            last=binding["last_synced_revision_id"] if binding else None
            tombstone_is_still_absent = (
                not active_revision_id and isinstance(last, str)
                and last.startswith("deleted:")
            )
            if last is not None and active_revision_id != last and not tombstone_is_still_absent:
                conflict_id=str(uuid4())
                conn.execute("""INSERT INTO sync_conflicts
                    (id,source_id,external_id,document_id,active_revision_id,
                     last_synced_revision_id,remote_revision,remote_metadata_json,status,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (conflict_id,source_id,document.external_id,document_id,active_revision_id,last,
                     document.revision,json.dumps(dict(document.metadata),sort_keys=True),"pending",time.time()))
                return "conflict", conflict_id
        revision_id=create_revision(document)
        self.bind(source_id,document.external_id,document_id,revision_id)
        return "applied", revision_id

    def list_pending(self, source_id: str) -> list[dict]:
        with self._connect() as conn:
            rows=conn.execute("SELECT * FROM sync_conflicts WHERE source_id=? AND status='pending' ORDER BY created_at",
                (source_id,)).fetchall()
        return [dict(row) for row in rows]

    def acknowledge(self, source_id: str, conflict_id: str) -> dict:
        """Mark a conflict handled after an operator resolves the document manually.

        This deliberately does not activate the remote revision. The sync layer must
        never turn an acknowledgement into an implicit content overwrite.
        """
        resolved_at = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE sync_conflicts
                   SET status='resolved',resolved_at=?,resolution='manually_resolved'
                   WHERE id=? AND source_id=? AND status='pending'""",
                (resolved_at, conflict_id, source_id),
            )
            if cursor.rowcount != 1:
                row = conn.execute(
                    "SELECT id FROM sync_conflicts WHERE id=? AND source_id=?",
                    (conflict_id, source_id),
                ).fetchone()
                if row is None:
                    raise KeyError(conflict_id)
            row = conn.execute("SELECT * FROM sync_conflicts WHERE id=?", (conflict_id,)).fetchone()
        return dict(row)


DocumentStateResolver = Callable[[object, SourceDocument], tuple[str, str] | None]
DocumentRevisionWriter = Callable[[object, str, SourceDocument], str]
DocumentIdentity = Callable[[object, SourceDocument], str]


class RevisionAwareDocumentApplier:
    """Bridge whole-document Connector changes to an atomic revision writer.

    The injected writer owns parsing, chunking, multi-index staging and active
    activation. Returning a revision id is its commit acknowledgement; until
    then neither the binding nor the datasource checkpoint may advance.
    """

    def __init__(
        self, conflicts: SyncConflictStore,
        resolve_current: DocumentStateResolver,
        write_revision: DocumentRevisionWriter,
        document_identity: DocumentIdentity | None = None,
    ) -> None:
        self.conflicts = conflicts
        self.resolve_current = resolve_current
        self.write_revision = write_revision
        self.document_identity = document_identity or (
            lambda source, document: str(uuid5(
                NAMESPACE_URL, f"skdy-sync:{source.id}:{document.external_id}",
            ))
        )

    def __call__(self, source, document: SourceDocument) -> str:
        existing = self.conflicts.binding(source.id, document.external_id)
        current = self.resolve_current(source, document)
        if current is None:
            document_id = existing["document_id"] if existing else self.document_identity(source, document)
            active_revision_id = ""
        else:
            document_id, active_revision_id = current
        outcome, _ = self.conflicts.apply_remote(
            source_id=source.id, document_id=document_id,
            active_revision_id=active_revision_id, document=document,
            create_revision=lambda item: self.write_revision(source, document_id, item),
        )
        if outcome == "conflict":
            return "conflict"
        if document.deleted:
            return "deleted"
        return "updated" if existing else "added"


__all__ = [
    "DocumentIdentity", "DocumentRevisionWriter", "DocumentStateResolver",
    "RevisionAwareDocumentApplier", "SyncConflictStore",
]
