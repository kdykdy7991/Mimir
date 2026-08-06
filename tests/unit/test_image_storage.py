"""
Tests for ImageStorage (C13).

Covers:
- Save: file exists on disk, record indexed, subdirs auto-created
- Idempotency: re-save same id updates in place (no duplicate row)
- get / get_path lookups
- find_by_collection / find_by_doc_hash batch queries
- WAL mode is enabled
- Default collection applied when none provided
- Extension normalization (".png" vs "png")
- Validation: empty bytes rejected
- delete: removes row + file by default
- Concurrent saves don't corrupt the DB
- Field preservation: doc_hash, page_num, collection
- Trace integration (n/a — kept simple; storage is fast)
"""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.ingestion.storage import (
    DEFAULT_BASE_DIR,
    DEFAULT_COLLECTION,
    DEFAULT_IMAGE_DB_PATH,
    ImageRecord,
    ImageStorage,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# A minimal valid PNG (1×1 transparent pixel). Enough to satisfy
# "non-empty bytes" without depending on any image library.
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"\xa3\x8c\x99\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def storage(tmp_path) -> ImageStorage:
    return ImageStorage(
        db_path=str(tmp_path / "image_index.db"),
        base_dir=str(tmp_path / "images"),
    )


# ---------------------------------------------------------------------------
# Basic save / file layout
# ---------------------------------------------------------------------------

class TestSave:
    def test_save_writes_file_to_disk(self, storage, tmp_path):
        rec = storage.save("img1", PNG_1X1, "png")
        assert Path(rec.file_path).exists()
        assert Path(rec.file_path).read_bytes() == PNG_1X1

    def test_save_under_collection_subdir(self, storage):
        rec = storage.save(
            "img2", PNG_1X1, "png", collection="docs"
        )
        assert "/docs/" in rec.file_path
        assert Path(rec.file_path).exists()

    def test_save_uses_default_collection_when_none(self, storage):
        rec = storage.save("img3", PNG_1X1, "png")
        assert rec.collection == DEFAULT_COLLECTION
        # File lives under the default subdir
        assert f"/{DEFAULT_COLLECTION}/" in rec.file_path

    def test_save_creates_subdirs_automatically(self, storage):
        storage.save(
            "img4", PNG_1X1, "png", collection="deep/nested/path"
        )
        rec = storage.get("img4")
        assert rec is not None
        assert Path(rec.file_path).exists()

    def test_save_extension_normalized(self, storage):
        rec1 = storage.save("img5", PNG_1X1, "png")
        rec2 = storage.save("img6", PNG_1X1, ".jpg")
        assert rec1.file_path.endswith(".png")
        assert rec2.file_path.endswith(".jpg")

    def test_save_records_metadata(self, storage):
        rec = storage.save(
            "img7", PNG_1X1, "png",
            collection="docs",
            doc_hash="doc_abc123",
            page_num=5,
        )
        assert rec.image_id == "img7"
        assert rec.collection == "docs"
        assert rec.doc_hash == "doc_abc123"
        assert rec.page_num == 5
        assert rec.created_at > 0

    def test_save_empty_bytes_raises(self, storage):
        with pytest.raises(ValueError, match="empty"):
            storage.save("img8", b"", "png")

    def test_default_paths_match_spec(self):
        """Spec: 'data/db/image_index.db' and 'data/images/{collection}/'."""
        assert DEFAULT_IMAGE_DB_PATH == "./data/db/image_index.db"
        assert DEFAULT_BASE_DIR == "./data/images"
        assert DEFAULT_COLLECTION == "_default"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_re_save_same_id_updates_not_duplicates(self, storage):
        rec1 = storage.save("imgA", PNG_1X1, "png", page_num=1)
        rec2 = storage.save("imgA", PNG_1X1, "png", page_num=2)
        # Same id and path (file is overwritten, path doesn't change)
        assert rec1.image_id == rec2.image_id
        assert rec1.file_path == rec2.file_path
        # Only ONE row in the index
        assert storage.count() == 1
        # The row reflects the latest metadata
        latest = storage.get("imgA")
        assert latest.page_num == 2

    def test_re_save_with_different_content_overwrites_file(
        self, storage
    ):
        new_bytes = PNG_1X1 + b"extra"
        rec = storage.save("imgB", PNG_1X1, "png")
        original_size = Path(rec.file_path).stat().st_size
        storage.save("imgB", new_bytes, "png")
        new_size = Path(rec.file_path).stat().st_size
        assert new_size != original_size
        assert Path(rec.file_path).read_bytes() == new_bytes


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

class TestLookups:
    def test_get_returns_record(self, storage):
        storage.save("img_lookup", PNG_1X1, "png", page_num=3)
        rec = storage.get("img_lookup")
        assert rec is not None
        assert rec.image_id == "img_lookup"
        assert rec.page_num == 3

    def test_get_missing_returns_none(self, storage):
        assert storage.get("never_saved") is None

    def test_get_path_convenience(self, storage):
        rec = storage.save("img_p", PNG_1X1, "png")
        path = storage.get_path("img_p")
        assert path == rec.file_path
        assert storage.get_path("missing") is None

    def test_count(self, storage):
        assert storage.count() == 0
        for i in range(3):
            storage.save(f"img_{i}", PNG_1X1, "png")
        assert storage.count() == 3


# ---------------------------------------------------------------------------
# Batch queries
# ---------------------------------------------------------------------------

class TestBatchQueries:
    def _seed(self, storage):
        storage.save("a1", PNG_1X1, "png", collection="docs", doc_hash="D1", page_num=1)
        storage.save("a2", PNG_1X1, "png", collection="docs", doc_hash="D1", page_num=2)
        storage.save("b1", PNG_1X1, "png", collection="imgs", doc_hash="D2", page_num=1)
        storage.save("c1", PNG_1X1, "png", collection="docs", doc_hash="D2", page_num=5)

    def test_find_by_collection(self, storage):
        self._seed(storage)
        docs = storage.find_by_collection("docs")
        assert len(docs) == 3
        assert {r.image_id for r in docs} == {"a1", "a2", "c1"}

    def test_find_by_collection_empty(self, storage):
        self._seed(storage)
        assert storage.find_by_collection("nonexistent") == []

    def test_find_by_collection_with_limit(self, storage):
        self._seed(storage)
        docs = storage.find_by_collection("docs", limit=2)
        assert len(docs) == 2

    def test_find_by_doc_hash(self, storage):
        self._seed(storage)
        d1 = storage.find_by_doc_hash("D1")
        assert len(d1) == 2
        assert {r.image_id for r in d1} == {"a1", "a2"}
        # Ordered by page_num asc
        assert d1[0].page_num <= d1[-1].page_num

    def test_find_by_doc_hash_empty(self, storage):
        self._seed(storage)
        assert storage.find_by_doc_hash("D_unknown") == []

    def test_find_by_doc_hash_with_limit(self, storage):
        self._seed(storage)
        d1 = storage.find_by_doc_hash("D1", limit=1)
        assert len(d1) == 1


# ---------------------------------------------------------------------------
# WAL mode + schema
# ---------------------------------------------------------------------------

class TestStorage:
    def test_wal_mode_enabled(self, storage):
        # Force schema creation by writing something.
        storage.save("wal_test", PNG_1X1, "png")
        conn = sqlite3.connect(storage.db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        assert mode.lower() == "wal"

    def test_schema_has_required_columns(self, storage):
        storage.save("schema_test", PNG_1X1, "png")
        conn = sqlite3.connect(storage.db_path)
        try:
            cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(image_index)"
                )
            }
        finally:
            conn.close()
        expected = {
            "image_id", "file_path", "collection",
            "doc_hash", "page_num", "created_at",
        }
        assert expected.issubset(cols)

    def test_indexes_created(self, storage):
        storage.save("idx_test", PNG_1X1, "png")
        conn = sqlite3.connect(storage.db_path)
        conn.row_factory = sqlite3.Row
        try:
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='image_index'"
                )
            }
        finally:
            conn.close()
        # Note: SQLite auto-creates an index for the PRIMARY KEY
        # ("sqlite_autoindex_image_index_1"); we just need our two
        # named indexes to exist.
        assert "idx_collection" in indexes
        assert "idx_doc_hash" in indexes

    def test_db_directory_created_automatically(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "img.db"
        s = ImageStorage(
            db_path=str(nested),
            base_dir=str(tmp_path / "imgs"),
        )
        s.save("auto_dir", PNG_1X1, "png")
        assert nested.exists()
        assert nested.parent.is_dir()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_removes_row_and_file(self, storage):
        rec = storage.save("to_del", PNG_1X1, "png")
        assert Path(rec.file_path).exists()
        ok = storage.delete("to_del")
        assert ok is True
        assert storage.get("to_del") is None
        assert not Path(rec.file_path).exists()

    def test_delete_missing_returns_false(self, storage):
        assert storage.delete("never_existed") is False

    def test_delete_keeps_row_if_remove_file_false(self, storage):
        rec = storage.save("keep", PNG_1X1, "png")
        storage.delete("keep", remove_file=False)
        # Row gone
        assert storage.get("keep") is None
        # But file still on disk
        assert Path(rec.file_path).exists()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_saves_all_persist(self, storage):
        """16 threads each save 5 images = 80 records. All must end up
        in the index, no 'database is locked' errors."""
        n_threads = 16
        n_per_thread = 5
        barrier = threading.Barrier(n_threads)

        def worker(i: int) -> None:
            barrier.wait()
            for j in range(n_per_thread):
                storage.save(
                    f"img_{i}_{j}", PNG_1X1, "png",
                    doc_hash=f"D_{i}", page_num=j,
                )

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker, i) for i in range(n_threads)]
            for f in futures:
                f.result()

        assert storage.count() == n_threads * n_per_thread

    def test_concurrent_mixed_save_and_query(self, storage):
        """Mixed workload — some threads write, some read."""
        n_writers = 8
        n_readers = 4
        barrier = threading.Barrier(n_writers + n_readers)

        def writer(i: int) -> None:
            barrier.wait()
            for j in range(5):
                storage.save(
                    f"w_{i}_{j}", PNG_1X1, "png",
                    doc_hash=f"wd_{i}",
                )

        def reader(i: int) -> None:
            barrier.wait()
            for _ in range(5):
                # Just exercise the query path; result may be 0 if
                # writers haven't finished, that's fine.
                storage.find_by_collection("wd_0")

        with ThreadPoolExecutor(max_workers=n_writers + n_readers) as pool:
            futs = (
                [pool.submit(writer, i) for i in range(n_writers)]
                + [pool.submit(reader, i) for i in range(n_readers)]
            )
            for f in futs:
                f.result()


# ---------------------------------------------------------------------------
# Type contract
# ---------------------------------------------------------------------------

class TestTypeContract:
    def test_save_returns_image_record(self, storage):
        rec = storage.save("typed", PNG_1X1, "png")
        assert isinstance(rec, ImageRecord)

    def test_get_returns_image_record_or_none(self, storage):
        assert storage.get("nope") is None
        storage.save("yes", PNG_1X1, "png")
        assert isinstance(storage.get("yes"), ImageRecord)
