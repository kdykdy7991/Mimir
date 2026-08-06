"""
ImageStorage (C13) — disk + SQLite index for extracted images.

The pipeline has two places images show up:

1. ``PdfLoader`` (C3) writes image bytes to disk as a side-effect
   of parsing a PDF, returning ``ImageRef`` objects.
2. ``ImageStorage`` (this class) is the *structured* layer: a
   single image id → file path mapping backed by SQLite, so the
   dashboard, the retriever, and the captioner can all look up
   images by id without scanning the filesystem.

Layout
------
On disk::

    {base_dir}/{collection_or__default}/{image_id}.{ext}

In SQLite (WAL mode, concurrent readers + single writer)::

    CREATE TABLE image_index (
        image_id    TEXT PRIMARY KEY,
        file_path   TEXT NOT NULL,
        collection  TEXT,
        doc_hash    TEXT,
        page_num    INTEGER,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_collection ON image_index(collection);
    CREATE INDEX idx_doc_hash   ON image_index(doc_hash);

Reuse of the C2 pattern
-----------------------
Same ``threading.Lock`` + short-lived connection + WAL
+ ``check_same_thread=False`` setup as
:class:`SQLiteIntegrityChecker` — see the comment block there
for the why.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# Default storage locations, matching the spec.
DEFAULT_DB_PATH = "./data/db/image_index.db"
DEFAULT_BASE_DIR = "./data/images"
DEFAULT_COLLECTION = "_default"  # used when the caller doesn't pass one


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass
class ImageRecord:
    """One row of the image index."""
    image_id: str
    file_path: str
    collection: str | None = None
    doc_hash: str | None = None
    page_num: int | None = None
    created_at: float = 0.0


# ---------------------------------------------------------------------------
# ImageStorage
# ---------------------------------------------------------------------------

class ImageStorage:
    """
    Save image bytes to disk + index them in SQLite.

    Args:
        db_path: SQLite file. Created on first use.
        base_dir: Root directory under which per-collection
            subdirectories are created.
    """

    name = "image_storage"

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        base_dir: str = DEFAULT_BASE_DIR,
    ) -> None:
        self.db_path = db_path
        self.base_dir = base_dir
        self._init_lock = threading.Lock()
        self._initialized = False

        # Ensure the DB parent exists; defer schema creation to the
        # first write so a no-op ImageStorage has zero IO cost.
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS image_index (
                        image_id    TEXT PRIMARY KEY,
                        file_path   TEXT NOT NULL,
                        collection  TEXT,
                        doc_hash    TEXT,
                        page_num    INTEGER,
                        created_at  REAL
                    )
                    """
                )
                # M3 migration: pre-M3 databases carry no ``collection``
                # column (rows predate per-collection scoping). ``CREATE
                # TABLE IF NOT EXISTS`` is a no-op for those, so add the
                # column explicitly rather than losing the legacy rows.
                cols = {
                    row["name"] for row in
                    conn.execute("PRAGMA table_info(image_index)").fetchall()
                }
                if "collection" not in cols:
                    conn.execute(
                        "ALTER TABLE image_index ADD COLUMN collection TEXT",
                    )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_collection "
                    "ON image_index(collection)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_doc_hash "
                    "ON image_index(doc_hash)"
                )
                conn.commit()
                self._initialized = True
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def save(
        self,
        image_id: str,
        image_bytes: bytes,
        ext: str = "png",
        *,
        collection: str | None = None,
        doc_hash: str | None = None,
        page_num: int | None = None,
    ) -> ImageRecord:
        """
        Write ``image_bytes`` to disk and index it.

        If the same ``image_id`` is saved again, the file is
        overwritten and the SQLite row is updated (idempotent).

        Returns the resulting :class:`ImageRecord`.
        """
        if not image_bytes:
            raise ValueError("image_bytes is empty")

        coll = collection or DEFAULT_COLLECTION
        # Strip a leading dot on the ext so callers can pass either
        # "png" or ".png" interchangeably.
        clean_ext = ext.lstrip(".") if ext else "png"

        target_dir = Path(self.base_dir) / coll
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{image_id}.{clean_ext}"
        target_path.write_bytes(image_bytes)

        self._ensure_schema()
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO image_index
                    (image_id, file_path, collection,
                     doc_hash, page_num, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(image_id) DO UPDATE SET
                    file_path   = excluded.file_path,
                    collection  = excluded.collection,
                    doc_hash    = excluded.doc_hash,
                    page_num    = excluded.page_num,
                    created_at  = excluded.created_at
                """,
                (image_id, str(target_path), coll, doc_hash,
                 page_num, now),
            )
            conn.commit()
        finally:
            conn.close()

        return ImageRecord(
            image_id=image_id,
            file_path=str(target_path),
            collection=coll,
            doc_hash=doc_hash,
            page_num=page_num,
            created_at=now,
        )

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get(self, image_id: str) -> ImageRecord | None:
        """Return the record for ``image_id`` or None if missing."""
        self._ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM image_index WHERE image_id = ?",
                (image_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_path(self, image_id: str) -> str | None:
        """Convenience — just the file path (or None)."""
        rec = self.get(image_id)
        return rec.file_path if rec else None

    def find_by_collection(
        self, collection: str, limit: int | None = None
    ) -> list[ImageRecord]:
        """All images in a collection, ordered by created_at desc."""
        self._ensure_schema()
        conn = self._connect()
        try:
            sql = (
                "SELECT * FROM image_index WHERE collection = ? "
                "ORDER BY created_at DESC"
            )
            params: tuple = (collection,)
            if limit is not None:
                sql += " LIMIT ?"
                params = (collection, limit)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._row_to_record(r) for r in rows]

    def find_by_doc_hash(
        self,
        doc_hash: str,
        *,
        collection: str | None = None,
        limit: int | None = None,
    ) -> list[ImageRecord]:
        """All images belonging to a source document hash.

        When ``collection`` is given the search is scoped to that
        collection — M3 protects callers from double-counting the
        same ``doc_hash`` ingested into two different collections.
        ``None`` returns hits from every collection (legacy
        behaviour).
        """
        self._ensure_schema()
        conn = self._connect()
        try:
            sql = "SELECT * FROM image_index WHERE doc_hash = ?"
            params: tuple = (doc_hash,)
            if collection is not None:
                sql += " AND collection = ?"
                params = params + (collection,)
            sql += " ORDER BY page_num ASC, created_at ASC"
            if limit is not None:
                sql += " LIMIT ?"
                params = params + (limit,)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._row_to_record(r) for r in rows]

    def count(self) -> int:
        """Total number of indexed images."""
        self._ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM image_index"
            ).fetchone()
        finally:
            conn.close()
        return int(row["n"])

    def delete(self, image_id: str, *, remove_file: bool = True) -> bool:
        """
        Remove an entry from the index. By default also deletes the
        file on disk. Returns True if a row was deleted.
        """
        self._ensure_schema()
        rec = self.get(image_id)
        if rec is None:
            return False
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM image_index WHERE image_id = ?",
                (image_id,),
            )
            conn.commit()
        finally:
            conn.close()
        if remove_file:
            try:
                Path(rec.file_path).unlink(missing_ok=True)
            except OSError:
                pass
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ImageRecord:
        return ImageRecord(
            image_id=row["image_id"],
            file_path=row["file_path"],
            collection=row["collection"],
            doc_hash=row["doc_hash"],
            page_num=row["page_num"],
            created_at=float(row["created_at"]),
        )

    def clear(self) -> None:  # pragma: no cover — test-only convenience
        """Wipe all rows. Intended for tests."""
        self._ensure_schema()
        conn = self._connect()
        try:
            conn.execute("DELETE FROM image_index")
            conn.commit()
        finally:
            conn.close()
