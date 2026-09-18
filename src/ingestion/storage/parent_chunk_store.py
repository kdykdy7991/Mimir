"""SQLite sidecar for staged/active parent-child document index versions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.core.types import Chunk


class ParentChunkStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredChunk:
    chunk_id: str
    text: str
    metadata: dict
    start_offset: int
    end_offset: int


class ParentChunkStore:
    """Version store whose active pointer changes in one SQLite transaction."""

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
                CREATE TABLE IF NOT EXISTS document_index_versions (
                    collection TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('staging','active','retired')),
                    PRIMARY KEY(collection, document_id, version)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_document_version
                    ON document_index_versions(collection, document_id)
                    WHERE status='active';
                CREATE TABLE IF NOT EXISTS parent_chunks (
                    collection TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    PRIMARY KEY(collection, document_id, version, chunk_id),
                    FOREIGN KEY(collection, document_id, version)
                        REFERENCES document_index_versions(collection, document_id, version)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS child_parent_links (
                    collection TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    child_id TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    PRIMARY KEY(collection, document_id, version, child_id),
                    FOREIGN KEY(collection, document_id, version, parent_id)
                        REFERENCES parent_chunks(collection, document_id, version, chunk_id)
                        ON DELETE CASCADE
                );
            """)

    def stage(
        self, *, collection: str, document_id: str, version: str,
        children: Iterable[Chunk], parents: Iterable[Chunk],
    ) -> bool:
        """Stage a version, returning false when it is already active.

        A forced re-index of byte-identical content keeps the active parent
        sidecar readable while dense/BM25 indexes are rebuilt.
        """
        child_rows, parent_rows = list(children), list(parents)
        parent_ids = {row.id for row in parent_rows}
        links = []
        for ordinal, row in enumerate(child_rows):
            parent_id = str(row.metadata.get("parent_chunk_id") or "")
            if not parent_id or parent_id not in parent_ids:
                raise ParentChunkStoreError(
                    f"child {row.id!r} has no parent in staged version",
                )
            links.append((row.id, parent_id, ordinal))
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT status FROM document_index_versions WHERE collection=? AND document_id=? AND version=?",
                (collection, document_id, version),
            ).fetchone()
            if existing is not None and existing["status"] == "active":
                return False
            conn.execute(
                "DELETE FROM document_index_versions WHERE collection=? AND document_id=? AND version=? AND status='staging'",
                (collection, document_id, version),
            )
            conn.execute(
                "INSERT INTO document_index_versions VALUES (?,?,?,'staging')",
                (collection, document_id, version),
            )
            conn.executemany(
                "INSERT INTO parent_chunks VALUES (?,?,?,?,?,?,?,?)",
                [(
                    collection, document_id, version, row.id, row.text,
                    json.dumps(row.metadata, ensure_ascii=False, sort_keys=True),
                    row.start_offset, row.end_offset,
                ) for row in parent_rows],
            )
            conn.executemany(
                "INSERT INTO child_parent_links VALUES (?,?,?,?,?,?)",
                [
                    (collection, document_id, version, child, parent, ordinal)
                    for child, parent, ordinal in links
                ],
            )
        return True

    def activate(self, *, collection: str, document_id: str, version: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM document_index_versions WHERE collection=? AND document_id=? AND version=?",
                (collection, document_id, version),
            ).fetchone()
            if row is None or row["status"] != "staging":
                raise ParentChunkStoreError("version is not staged")
            missing = conn.execute("""
                SELECT COUNT(*) AS n FROM child_parent_links AS l
                LEFT JOIN parent_chunks AS p
                  ON p.collection=l.collection AND p.document_id=l.document_id
                 AND p.version=l.version AND p.chunk_id=l.parent_id
                WHERE l.collection=? AND l.document_id=? AND l.version=?
                  AND p.chunk_id IS NULL
            """, (collection, document_id, version)).fetchone()["n"]
            if missing:
                raise ParentChunkStoreError("staged version has dangling parent links")
            conn.execute(
                "UPDATE document_index_versions SET status='retired' WHERE collection=? AND document_id=? AND status='active'",
                (collection, document_id),
            )
            conn.execute(
                "UPDATE document_index_versions SET status='active' WHERE collection=? AND document_id=? AND version=?",
                (collection, document_id, version),
            )

    def active_version(self, collection: str, document_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT version FROM document_index_versions WHERE collection=? AND document_id=? AND status='active'",
                (collection, document_id),
            ).fetchone()
        return str(row["version"]) if row else None

    def restore_active(
        self, collection: str, document_id: str, version: str | None,
    ) -> None:
        """Compensate a failed cross-index activation in one transaction."""
        with self._connect() as conn:
            if version is not None:
                row = conn.execute(
                    "SELECT 1 FROM document_index_versions WHERE collection=? AND document_id=? AND version=?",
                    (collection, document_id, version),
                ).fetchone()
                if row is None:
                    raise ParentChunkStoreError("restore version does not exist")
            conn.execute(
                "UPDATE document_index_versions SET status='retired' WHERE collection=? AND document_id=? AND status='active'",
                (collection, document_id),
            )
            if version is not None:
                conn.execute(
                    "UPDATE document_index_versions SET status='active' WHERE collection=? AND document_id=? AND version=?",
                    (collection, document_id, version),
                )

    def parent_for_child(
        self, collection: str, document_id: str, child_id: str,
    ) -> StoredChunk | None:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT p.* FROM document_index_versions AS v
                JOIN child_parent_links AS l USING(collection, document_id, version)
                JOIN parent_chunks AS p
                  ON p.collection=l.collection AND p.document_id=l.document_id
                 AND p.version=l.version AND p.chunk_id=l.parent_id
                WHERE v.collection=? AND v.document_id=? AND v.status='active'
                  AND l.child_id=?
            """, (collection, document_id, child_id)).fetchone()
        if row is None:
            return None
        return StoredChunk(
            chunk_id=str(row["chunk_id"]), text=str(row["text"]),
            metadata=json.loads(row["metadata_json"]),
            start_offset=int(row["start_offset"]), end_offset=int(row["end_offset"]),
        )

    def delete_staging(self, collection: str, document_id: str, version: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM document_index_versions WHERE collection=? AND document_id=? AND version=? AND status='staging'",
                (collection, document_id, version),
            )

    def delete_document(self, collection: str, document_id: str) -> None:
        """Idempotently remove every sidecar version after document deletion."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM document_index_versions WHERE collection=? AND document_id=?",
                (collection, document_id),
            )


__all__ = ["ParentChunkStore", "ParentChunkStoreError", "StoredChunk"]
