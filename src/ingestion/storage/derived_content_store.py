"""Independent namespace for removable/rebuildable derived content."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from src.application.contracts import DerivedArtifact, DerivedKind


class DerivedContentStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS derived_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('summary','synthetic_question')),
                    collection TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    weight REAL NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    created_at TEXT NOT NULL,
                    UNIQUE(kind, revision_id, text, model, prompt_version)
                );
                CREATE INDEX IF NOT EXISTS ix_derived_source
                    ON derived_artifacts(collection, document_id, chunk_id, revision_id);
            """)

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def replace_revision(self, revision_id: str, artifacts: list[DerivedArtifact]) -> None:
        if any(row.revision_id != revision_id for row in artifacts):
            raise ValueError("all artifacts must belong to the replaced revision")
        with self._connect() as conn:
            conn.execute("DELETE FROM derived_artifacts WHERE revision_id=?", (revision_id,))
            conn.executemany(
                "INSERT INTO derived_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(
                    row.artifact_id, row.kind.value, row.collection,
                    row.document_id, row.chunk_id, row.revision_id, row.text,
                    row.model, row.prompt_version, row.weight,
                    int(row.enabled), row.created_at.isoformat(),
                ) for row in artifacts],
            )

    def list(self, revision_id: str, *, enabled_only: bool = False) -> list[DerivedArtifact]:
        sql = "SELECT * FROM derived_artifacts WHERE revision_id=?"
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY kind, artifact_id"
        with self._connect() as conn:
            rows = conn.execute(sql, (revision_id,)).fetchall()
        return [self._row(row) for row in rows]

    def set_enabled(self, artifact_id: str, enabled: bool) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE derived_artifacts SET enabled=? WHERE artifact_id=?",
                (int(enabled), artifact_id),
            )
            if cur.rowcount != 1:
                raise KeyError("derived artifact not found")

    def delete_revision(self, revision_id: str) -> int:
        with self._connect() as conn:
            return conn.execute(
                "DELETE FROM derived_artifacts WHERE revision_id=?", (revision_id,),
            ).rowcount

    @staticmethod
    def _row(row) -> DerivedArtifact:
        return DerivedArtifact(
            artifact_id=row["artifact_id"], kind=DerivedKind(row["kind"]),
            collection=row["collection"], document_id=row["document_id"],
            chunk_id=row["chunk_id"], revision_id=row["revision_id"],
            text=row["text"], model=row["model"], prompt_version=row["prompt_version"],
            weight=float(row["weight"]), enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


__all__ = ["DerivedContentStore"]
