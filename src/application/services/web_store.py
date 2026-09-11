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

One database file, six tables:

- ``tasks``                  — durable task records (ingestion + query)
- ``traces``                 — indexed trace payloads (JSON)
- ``query_results``          — async query output (serialized QueryResult)
- ``query_citations``        — ``(query_id, document_id)`` joins for
  ``DocumentDetail.last_query_id``
- ``collections``            — persistent knowledge-base descriptions
- ``embedding_usage_events`` — per-call embedding token usage (PRD
  ``docs/prd-embedding-token-metrics.md`` §5.3) feeding the overview's
  token cards

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

# Controlled tag colour tokens (B2.1). Real colours are mapped by the UI;
# the server only accepts these, never arbitrary CSS.
TAG_COLORS = frozenset({"grey", "blue", "green", "red", "purple", "amber"})

# Maximum logical folder depth (root = depth 0). Hard cap (B2.4).
MAX_FOLDER_DEPTH = 5


class FolderNotEmptyError(Exception):
    """Raised when deleting a folder that still contains children/documents.

    Task book B2.3: non-empty folders cannot be deleted in v1 (no recursive
    force-delete); callers must move items out first.
    """


def normalize_tag_name(name: str) -> str:
    """Whitespace-stripped, case-folded uniqueness key for a tag name."""
    return name.strip().casefold()


def normalize_folder_name(name: str) -> str:
    """Whitespace-stripped, case-folded uniqueness key for a folder name."""
    return name.strip().casefold()


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
                    created_at  REAL NOT NULL,
                    source      TEXT NOT NULL DEFAULT "web_api",
                    api_key_id  TEXT
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

                CREATE TABLE IF NOT EXISTS embedding_usage_events (
                    id                  TEXT PRIMARY KEY,
                    occurred_at         REAL NOT NULL,
                    operation           TEXT NOT NULL,
                    token_count         INTEGER NOT NULL,
                    provider            TEXT NOT NULL,
                    model               TEXT NOT NULL,
                    collection_id       TEXT,
                    trace_id            TEXT,
                    task_id             TEXT,
                    document_id         TEXT,
                    provider_request_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_usage_occurred
                    ON embedding_usage_events(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_usage_op_occurred
                    ON embedding_usage_events(operation, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_usage_col_occurred
                    ON embedding_usage_events(collection_id, occurred_at);

                -- Knowledge-management logical tables (task book B2).
                -- Tags are collection-scoped; the normalized name is unique
                -- within a collection. Links are a pure join (tag→docs).
                CREATE TABLE IF NOT EXISTS document_tags (
                    tag_id          TEXT PRIMARY KEY,
                    collection_id   TEXT NOT NULL,
                    name            TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    color           TEXT NOT NULL DEFAULT 'grey',
                    created_at      REAL NOT NULL,
                    updated_at      REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_document_tags_col_name
                    ON document_tags(collection_id, normalized_name);

                CREATE TABLE IF NOT EXISTS document_tag_links (
                    document_id TEXT NOT NULL,
                    tag_id      TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    PRIMARY KEY (document_id, tag_id)
                );
                CREATE INDEX IF NOT EXISTS idx_tag_links_tag
                    ON document_tag_links(tag_id);

                -- Logical document folders (task book B2.3). A plain tree:
                -- root is expressed by parent_id NULL (no fake root row).
                CREATE TABLE IF NOT EXISTS document_folders (
                    folder_id       TEXT PRIMARY KEY,
                    collection_id   TEXT NOT NULL,
                    parent_id       TEXT,
                    name            TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    depth           INTEGER NOT NULL DEFAULT 0,
                    created_at      REAL NOT NULL,
                    updated_at      REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_col_parent_name
                    ON document_folders(collection_id, parent_id, normalized_name);
                CREATE INDEX IF NOT EXISTS idx_folders_parent
                    ON document_folders(parent_id);

                -- Nullable per-document folder placement (``folder_id`` NULL
                -- means the document sits at the collection root).
                CREATE TABLE IF NOT EXISTS document_placements (
                    document_id     TEXT PRIMARY KEY,
                    folder_id       TEXT,
                    collection_id   TEXT NOT NULL,
                    updated_at      REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_placements_folder
                    ON document_placements(folder_id);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(query_results)")}
            if "source" not in columns:
                conn.execute("ALTER TABLE query_results ADD COLUMN source TEXT NOT NULL DEFAULT \"web_api\"")
            if "api_key_id" not in columns:
                conn.execute("ALTER TABLE query_results ADD COLUMN api_key_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_query_results_source_created ON query_results(source, created_at)")
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
    # Folders (task book B2.3 / B2.4)
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_folder_name(name: str) -> str:
        stripped = name.strip()
        if not 1 <= len(stripped) <= 64:
            raise ValueError("folder name must be 1-64 characters after trimming")
        return stripped

    def get_folder(self, folder_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM document_folders WHERE folder_id = ?", (folder_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def create_folder(
        self, *, collection_id: str, name: str, parent_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a folder; siblings must have unique normalized names.

        ``parent_id`` is validated to exist in the same collection and the
        resulting depth must not exceed :data:`MAX_FOLDER_DEPTH`. Raises
        ``ValueError`` on invalid input and ``sqlite3.IntegrityError`` on a
        sibling-name collision.
        """
        import time
        from uuid import uuid4

        cleaned = self._validate_folder_name(name)
        depth = 0
        if parent_id is not None:
            parent = self.get_folder(parent_id)
            if parent is None:
                raise ValueError("parent folder does not exist")
            depth = int(parent["depth"]) + 1
            if depth > MAX_FOLDER_DEPTH:
                raise ValueError(f"folder depth exceeds the maximum {MAX_FOLDER_DEPTH}")
        folder_id = str(uuid4())
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO document_folders
                    (folder_id, collection_id, parent_id, name, normalized_name,
                     depth, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (folder_id, collection_id, parent_id, cleaned,
                 normalize_folder_name(cleaned), depth, now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_folder(folder_id)  # type: ignore[return-value]

    def list_folders(self, collection_id: str) -> list[dict[str, Any]]:
        """All folders in a collection (flat, stable; tree built by clients)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM document_folders
                WHERE collection_id = ?
                ORDER BY depth ASC, normalized_name ASC, created_at ASC, folder_id ASC
                """,
                (collection_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def rename_folder(self, folder_id: str, name: str) -> dict[str, Any] | None:
        import time

        if self.get_folder(folder_id) is None:
            return None
        cleaned = self._validate_folder_name(name)
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE document_folders
                   SET name = ?, normalized_name = ?, updated_at = ?
                 WHERE folder_id = ?
                """,
                (cleaned, normalize_folder_name(cleaned), time.time(), folder_id),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_folder(folder_id)

    def _descendant_folder_ids(self, conn, folder_id: str) -> set[str]:
        """All folder ids strictly below ``folder_id`` (cycle check helper)."""
        descendants: set[str] = set()
        frontier = [folder_id]
        while frontier:
            nxt: list[str] = []
            for fid in frontier:
                rows = conn.execute(
                    "SELECT folder_id FROM document_folders WHERE parent_id = ?", (fid,),
                ).fetchall()
                for r in rows:
                    cid = r["folder_id"]
                    if cid not in descendants:
                        descendants.add(cid)
                        nxt.append(cid)
            frontier = nxt
        return descendants

    def move_folder(
        self, *, folder_id: str, new_parent_id: str | None,
    ) -> dict[str, Any] | None:
        """Re-parent a folder (``new_parent_id`` None = root) with depth/cycle checks."""
        import time

        folder = self.get_folder(folder_id)
        if folder is None:
            return None
        conn = self._connect()
        try:
            if new_parent_id is not None:
                parent = self.get_folder(new_parent_id)
                if parent is None or parent["collection_id"] != folder["collection_id"]:
                    raise ValueError("target parent folder does not exist in the collection")
                # cycle: moving a folder under one of its own descendants
                descendants = self._descendant_folder_ids(conn, folder_id)
                if new_parent_id in descendants:
                    raise ValueError("cannot move a folder into its own descendant")
            new_depth = 0 if new_parent_id is None else int(
                self.get_folder(new_parent_id)["depth"]
            ) + 1
            if new_depth > MAX_FOLDER_DEPTH:
                raise ValueError(f"folder depth exceeds the maximum {MAX_FOLDER_DEPTH}")
            conn.execute(
                """
                UPDATE document_folders SET parent_id = ?, updated_at = ? WHERE folder_id = ?
                """,
                (new_parent_id, time.time(), folder_id),
            )
            # recompute depth for the moved subtree
            self._recompute_depth(conn, folder_id, new_depth)
            conn.commit()
        finally:
            conn.close()
        return self.get_folder(folder_id)

    def _recompute_depth(self, conn, folder_id: str, depth: int) -> None:
        conn.execute(
            "UPDATE document_folders SET depth = ? WHERE folder_id = ?", (depth, folder_id),
        )
        for row in conn.execute(
            "SELECT folder_id FROM document_folders WHERE parent_id = ?", (folder_id,),
        ).fetchall():
            self._recompute_depth(conn, row["folder_id"], depth + 1)

    def delete_folder(self, folder_id: str) -> dict[str, Any]:
        """Delete an *empty* folder; ``FolderNotEmptyError`` when non-empty.

        Task book B2.3: v1 does no recursive force-delete — a folder that
        still contains children or documents is rejected so callers move
        items out first.
        """
        import time

        folder = self.get_folder(folder_id)
        if folder is None:
            return {"reparented_folders": 0, "reparented_documents": 0}
        conn = self._connect()
        try:
            child_rows = conn.execute(
                "SELECT folder_id FROM document_folders WHERE parent_id = ?", (folder_id,),
            ).fetchall()
            child_count = len(child_rows)
            doc_row = conn.execute(
                "SELECT COUNT(*) AS n FROM document_placements WHERE folder_id = ?",
                (folder_id,),
            ).fetchone()
            doc_count = int(doc_row["n"]) if doc_row else 0
            if child_count > 0 or doc_count > 0:
                raise FolderNotEmptyError(
                    f"folder {folder_id!r} is not empty "
                    f"({child_count} subfolders, {doc_count} documents)",
                )
            conn.execute("DELETE FROM document_folders WHERE folder_id = ?", (folder_id,))
            conn.commit()
            return {"reparented_folders": 0, "reparented_documents": 0}
        finally:
            conn.close()

    def move_document(self, *, document_id: str, folder_id: str | None, collection_id: str) -> None:
        """Set (or clear) a document's folder placement."""
        import time

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO document_placements (document_id, folder_id, collection_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE
                    SET folder_id = excluded.folder_id,
                        collection_id = excluded.collection_id,
                        updated_at = excluded.updated_at
                """,
                (document_id, folder_id, collection_id, time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    def document_placement(self, document_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM document_placements WHERE document_id = ?", (document_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def documents_by_folder(self, folder_id: str | None, collection_id: str) -> list[str]:
        """Document ids placed in ``folder_id`` (None = collection root)."""
        conn = self._connect()
        try:
            if folder_id is None:
                rows = conn.execute(
                    "SELECT document_id FROM document_placements WHERE collection_id = ? AND folder_id IS NULL",
                    (collection_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT document_id FROM document_placements WHERE folder_id = ?", (folder_id,),
                ).fetchall()
        finally:
            conn.close()
        return [r["document_id"] for r in rows]

    def count_documents_in_folder(self, folder_id: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM document_placements WHERE folder_id = ?",
                (folder_id,),
            ).fetchone()
        finally:
            conn.close()
        return int(row["n"] if row else 0)

    # ------------------------------------------------------------------
    # Tags (task book B2.1 / B2.2)
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_tag_name(name: str) -> str:
        stripped = name.strip()
        if not 1 <= len(stripped) <= 64:
            raise ValueError(f"tag name must be 1-64 characters after trimming, got {len(stripped)}")
        return stripped

    @staticmethod
    def _validate_tag_color(color: str) -> str:
        if color not in TAG_COLORS:
            raise ValueError(f"tag color {color!r} is not an allowed token")
        return color

    def create_tag(
        self, *, collection_id: str, name: str, color: str = "grey",
    ) -> dict[str, Any]:
        """Create a collection-scoped tag; unique normalized name per collection.

        Raises ``ValueError`` on invalid name/colour and ``sqlite3.IntegrityError``
        when ``normalized_name`` already exists in the collection.
        """
        import time
        from uuid import uuid4

        cleaned = self._validate_tag_name(name)
        color = self._validate_tag_color(color)
        tag_id = str(uuid4())
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO document_tags
                    (tag_id, collection_id, name, normalized_name, color,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tag_id, collection_id, cleaned, normalize_tag_name(cleaned),
                 color, now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_tag(tag_id)  # type: ignore[return-value]

    def get_tag(self, tag_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM document_tags WHERE tag_id = ?", (tag_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def list_tags(self, collection_id: str) -> list[dict[str, Any]]:
        """Tags in a collection, stable order (name asc, then created)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM document_tags
                WHERE collection_id = ?
                ORDER BY normalized_name ASC, created_at ASC, tag_id ASC
                """,
                (collection_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def update_tag(
        self, *, tag_id: str, name: str | None = None, color: str | None = None,
    ) -> dict[str, Any] | None:
        """Rename and/or recolour a tag; recomputes the normalized name."""
        import time

        existing = self.get_tag(tag_id)
        if existing is None:
            return None
        new_name = existing["name"] if name is None else self._validate_tag_name(name)
        new_color = existing["color"] if color is None else self._validate_tag_color(color)
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE document_tags
                   SET name = ?, normalized_name = ?, color = ?, updated_at = ?
                 WHERE tag_id = ?
                """,
                (new_name, normalize_tag_name(new_name), new_color, time.time(), tag_id),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_tag(tag_id)

    def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag and its links (never the documents)."""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM document_tag_links WHERE tag_id = ?", (tag_id,))
            cur = conn.execute("DELETE FROM document_tags WHERE tag_id = ?", (tag_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_document_tags(self, document_id: str, tag_ids: list[str]) -> int:
        """Transactionally replace a document's tags; returns link count."""
        import time

        ids = list(dict.fromkeys(tag_ids))
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("DELETE FROM document_tag_links WHERE document_id = ?", (document_id,))
            conn.executemany(
                """
                INSERT OR IGNORE INTO document_tag_links (document_id, tag_id, created_at)
                VALUES (?, ?, ?)
                """,
                [(document_id, tid, now) for tid in ids],
            )
            conn.commit()
            return len(ids)
        finally:
            conn.close()

    def document_tag_ids(self, document_id: str) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT tag_id FROM document_tag_links WHERE document_id = ? ORDER BY created_at ASC",
                (document_id,),
            ).fetchall()
        finally:
            conn.close()
        return [r["tag_id"] for r in rows]

    def count_tag_links(self, tag_id: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM document_tag_links WHERE tag_id = ?",
                (tag_id,),
            ).fetchone()
        finally:
            conn.close()
        return int(row["n"] if row else 0)

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
        source: str = "web_api",
        api_key_id: str | None = None,
    ) -> None:
        """Persist an async query's output + the documents it cited."""
        import time

        created = created_at if created_at is not None else time.time()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO query_results
                    (query_id, collection, query_text, result_json, created_at, source, api_key_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(query_id) DO UPDATE SET
                    collection  = excluded.collection,
                    query_text  = excluded.query_text,
                    result_json = excluded.result_json,
                    created_at  = excluded.created_at,
                    source      = excluded.source,
                    api_key_id  = excluded.api_key_id
                """,
                (query_id, collection, query_text, result_json, created, source, api_key_id),
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
        self, start_at: float, end_at: float, *, source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query result rows in a half-open reporting interval."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT query_id, collection, query_text, result_json, created_at
                FROM query_results
                WHERE created_at >= ? AND created_at < ?
                  AND (? IS NULL OR source = ?)
                ORDER BY created_at ASC
                """,
                (start_at, end_at, source, source),
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

    # ------------------------------------------------------------------
    # Embedding token usage (PRD docs/prd-embedding-token-metrics.md §5.3)
    # ------------------------------------------------------------------
    def record_embedding_usage(self, event: dict[str, Any]) -> None:
        """Insert one embedding usage event (best-effort by caller).

        ``id`` is the dedup key — the store sets it from the provider
        request id when available so an app-level retry of the same
        provider call is never double-counted. ``INSERT OR IGNORE``
        keeps a duplicate write idempotent.
        """
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO embedding_usage_events
                    (id, occurred_at, operation, token_count, provider,
                     model, collection_id, trace_id, task_id, document_id,
                     provider_request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"], event["occurred_at"], event["operation"],
                    event["token_count"], event["provider"], event["model"],
                    event.get("collection_id"), event.get("trace_id"),
                    event.get("task_id"), event.get("document_id"),
                    event.get("provider_request_id"),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def summarize_embedding_usage(
        self, start_at: float, end_at: float,
    ) -> tuple[int, int, int]:
        """Sum token usage in a half-open interval, split by operation.

        Returns ``(total, query_total, ingestion_total)``. Empty ranges
        yield ``(0, 0, 0)`` — the caller decides whether ``0`` or ``null``
        is the honest answer based on capability.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                    SUM(token_count) AS total,
                    SUM(CASE WHEN operation = 'query'
                        THEN token_count ELSE 0 END) AS query_total,
                    SUM(CASE WHEN operation = 'ingestion'
                        THEN token_count ELSE 0 END) AS ingestion_total
                FROM embedding_usage_events
                WHERE occurred_at >= ? AND occurred_at < ?
                """,
                (start_at, end_at),
            ).fetchone()
        finally:
            conn.close()
        if row is None or row["total"] is None:
            return (0, 0, 0)
        return (int(row["total"]), int(row["query_total"]), int(row["ingestion_total"]))

    def embedding_usage_earliest(self) -> float | None:
        """UTC epoch of the earliest recorded usage event (or ``None``)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT MIN(occurred_at) AS earliest FROM embedding_usage_events",
            ).fetchone()
        finally:
            conn.close()
        return float(row["earliest"]) if row is not None and row["earliest"] is not None else None

    def ping(self) -> None:
        """Liveness probe — raises if the SQLite file is unreadable."""
        conn = self._connect()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()


__all__ = [
    "DEFAULT_DB_PATH", "MAX_FOLDER_DEPTH", "TAG_COLORS", "WebApiDB",
    "FolderNotEmptyError", "normalize_folder_name", "normalize_tag_name",
]
