"""Durable, reviewable model suggestions; never mutates manual tags."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.application.contracts import SuggestionStatus, TagSuggestion


class EnrichmentStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tag_suggestions (
                    suggestion_id TEXT PRIMARY KEY,
                    collection TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    suggested_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    existing_tag_id TEXT,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    reviewer TEXT,
                    UNIQUE(document_id, revision_id, normalized_name, model, prompt_version)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, suggestion: TagSuggestion, normalized_name: str) -> TagSuggestion:
        with self._connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO tag_suggestions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                suggestion.suggestion_id, suggestion.collection,
                suggestion.document_id, suggestion.revision_id,
                suggestion.suggested_name, normalized_name,
                suggestion.existing_tag_id, suggestion.model,
                suggestion.prompt_version, suggestion.confidence,
                suggestion.status.value, suggestion.created_at.isoformat(),
                None, None,
            ))
            row = conn.execute("""
                SELECT * FROM tag_suggestions
                WHERE document_id=? AND revision_id=? AND normalized_name=?
                  AND model=? AND prompt_version=?
            """, (
                suggestion.document_id, suggestion.revision_id, normalized_name,
                suggestion.model, suggestion.prompt_version,
            )).fetchone()
        return self._row(row)

    def list(self, document_id: str, revision_id: str | None = None) -> list[TagSuggestion]:
        sql = "SELECT * FROM tag_suggestions WHERE document_id=?"
        args: list[str] = [document_id]
        if revision_id is not None:
            sql += " AND revision_id=?"
            args.append(revision_id)
        sql += " ORDER BY created_at, suggestion_id"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._row(row) for row in rows]

    def get(self, suggestion_id: str) -> TagSuggestion:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tag_suggestions WHERE suggestion_id=?",
                (suggestion_id,),
            ).fetchone()
        if row is None:
            raise KeyError("tag suggestion not found")
        return self._row(row)

    def review(
        self, suggestion_id: str, status: SuggestionStatus,
        reviewer: str, *, existing_tag_id: str | None = None,
    ) -> TagSuggestion:
        if status is SuggestionStatus.PENDING:
            raise ValueError("review status must be approved or rejected")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tag_suggestions WHERE suggestion_id=?",
                (suggestion_id,),
            ).fetchone()
            if row is None:
                raise KeyError("tag suggestion not found")
            if row["status"] != SuggestionStatus.PENDING.value:
                raise ValueError("tag suggestion was already reviewed")
            tag_id = existing_tag_id or row["existing_tag_id"]
            if status is SuggestionStatus.APPROVED and not tag_id:
                raise ValueError("approval requires an existing tag id")
            conn.execute("""
                UPDATE tag_suggestions
                SET status=?, existing_tag_id=?, reviewed_at=?, reviewer=?
                WHERE suggestion_id=?
            """, (status.value, tag_id, now, reviewer, suggestion_id))
            updated = conn.execute(
                "SELECT * FROM tag_suggestions WHERE suggestion_id=?",
                (suggestion_id,),
            ).fetchone()
        return self._row(updated)

    @staticmethod
    def _row(row: sqlite3.Row) -> TagSuggestion:
        return TagSuggestion(
            suggestion_id=row["suggestion_id"], collection=row["collection"],
            document_id=row["document_id"], revision_id=row["revision_id"],
            suggested_name=row["suggested_name"], existing_tag_id=row["existing_tag_id"],
            model=row["model"], prompt_version=row["prompt_version"],
            confidence=float(row["confidence"]), status=SuggestionStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            reviewed_at=(datetime.fromisoformat(row["reviewed_at"]) if row["reviewed_at"] else None),
            reviewer=row["reviewer"],
        )


__all__ = ["EnrichmentStore"]
