"""
Unit tests for FileIntegrityChecker (C2).

Covers:
- SHA256 stability / determinism / streaming
- ``should_skip`` semantics (success → skip, failed → retry)
- ``mark_success`` / ``mark_failed`` upserts
- Database file created at the expected location
- SQLite WAL mode is enabled
- Concurrent writers do not corrupt the database
- ``get_record`` round-trip
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.libs.loader.file_integrity import (
    DEFAULT_DB_PATH,
    FileIntegrityChecker,
    IngestionRecord,
    MissingFileError,
    SQLiteIntegrityChecker,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Yield a fresh SQLiteIntegrityChecker pointed at a tmp database."""
    db_path = tmp_path / "ingestion_history.db"
    yield SQLiteIntegrityChecker(str(db_path)), str(db_path)


@pytest.fixture
def sample_file(tmp_path):
    """Write a small text file and return its path."""
    p = tmp_path / "sample.txt"
    p.write_text("hello modular rag\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def large_file(tmp_path):
    """Write a 2 MiB file (forces streaming in SHA256)."""
    p = tmp_path / "large.bin"
    # 2 MiB of zero bytes — content is irrelevant, we just need >64 KiB
    # to exercise the streaming path (chunk size in the implementation).
    p.write_bytes(b"\x00" * (2 * 1024 * 1024))
    return str(p)


@pytest.fixture
def empty_file(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_bytes(b"")
    return str(p)


# ---------------------------------------------------------------------------
# SHA256 hashing
# ---------------------------------------------------------------------------

class TestComputeSha256:
    """The hash function must be deterministic and stream-based."""

    def test_hash_is_deterministic(self, tmp_db, sample_file):
        checker, _ = tmp_db
        h1 = checker.compute_sha256(sample_file)
        h2 = checker.compute_sha256(sample_file)
        assert h1 == h2
        # Real SHA256 hex digest length
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_hash_matches_hashlib(self, tmp_db, sample_file):
        """Our streaming hash must equal hashlib's on the same content."""
        checker, _ = tmp_db
        expected = hashlib.sha256(b"hello modular rag\n").hexdigest()
        assert checker.compute_sha256(sample_file) == expected

    def test_different_content_different_hash(self, tmp_db, tmp_path):
        checker, _ = tmp_db
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("alpha", encoding="utf-8")
        b.write_text("beta", encoding="utf-8")
        assert checker.compute_sha256(str(a)) != checker.compute_sha256(str(b))

    def test_streams_large_files(self, tmp_db, large_file):
        """Large file must hash without loading it all into memory."""
        checker, _ = tmp_db
        h = checker.compute_sha256(large_file)
        # SHA256 of 2 MiB of zero bytes is a known constant.
        expected = hashlib.sha256(b"\x00" * (2 * 1024 * 1024)).hexdigest()
        assert h == expected

    def test_empty_file(self, tmp_db, empty_file):
        checker, _ = tmp_db
        # SHA256 of empty input
        assert checker.compute_sha256(empty_file) == hashlib.sha256(b"").hexdigest()

    def test_missing_file_raises(self, tmp_db, tmp_path):
        checker, _ = tmp_db
        ghost = tmp_path / "does_not_exist.txt"
        with pytest.raises(MissingFileError) as exc:
            checker.compute_sha256(str(ghost))
        assert "does_not_exist" in str(exc.value)

    def test_hash_directory_raises(self, tmp_db, tmp_path):
        """Hashing a directory must fail, not silently return a digest."""
        checker, _ = tmp_db
        with pytest.raises(MissingFileError):
            checker.compute_sha256(str(tmp_path))


# ---------------------------------------------------------------------------
# should_skip semantics
# ---------------------------------------------------------------------------

class TestShouldSkip:
    """``should_skip`` returns True iff a previous run terminated successfully."""

    def test_unknown_hash_is_not_skipped(self, tmp_db):
        checker, _ = tmp_db
        assert checker.should_skip("0" * 64) is False

    def test_success_is_skipped(self, tmp_db, sample_file):
        checker, _ = tmp_db
        h = checker.compute_sha256(sample_file)
        assert checker.should_skip(h) is False  # before mark_success
        checker.mark_success(h, sample_file)
        assert checker.should_skip(h) is True   # after mark_success

    def test_failure_is_not_skipped(self, tmp_db, sample_file):
        """A previous failure must remain retriable on the next run."""
        checker, _ = tmp_db
        h = checker.compute_sha256(sample_file)
        checker.mark_failed(h, sample_file, error_msg="boom")
        assert checker.should_skip(h) is False

    def test_failure_then_success_is_skipped(self, tmp_db, sample_file):
        """Retry after a failure: mark_success should flip should_skip → True."""
        checker, _ = tmp_db
        h = checker.compute_sha256(sample_file)
        checker.mark_failed(h, sample_file, error_msg="boom")
        assert checker.should_skip(h) is False
        checker.mark_success(h, sample_file)
        assert checker.should_skip(h) is True

    def test_success_then_failure_is_not_skipped(self, tmp_db, sample_file):
        """If a previously-good file is re-ingested and fails, it should
        become retriable again (downgrade behavior)."""
        checker, _ = tmp_db
        h = checker.compute_sha256(sample_file)
        checker.mark_success(h, sample_file)
        assert checker.should_skip(h) is True
        checker.mark_failed(h, sample_file, error_msg="regression")
        assert checker.should_skip(h) is False

    def test_mark_success_is_idempotent(self, tmp_db, sample_file):
        checker, _ = tmp_db
        h = checker.compute_sha256(sample_file)
        checker.mark_success(h, sample_file)
        checker.mark_success(h, sample_file)  # second call must not raise
        assert checker.should_skip(h) is True


# ---------------------------------------------------------------------------
# Storage location & schema
# ---------------------------------------------------------------------------

class TestStorage:
    """Database file is created at the expected path with the right schema."""

    def test_db_file_created_at_configured_path(self, tmp_db):
        _, db_path = tmp_db
        # Force a write so the file materializes.
        checker = tmp_db[0]
        checker.mark_success("deadbeef" * 8, "/tmp/x.pdf")
        assert os.path.exists(db_path)

    def test_default_db_path(self):
        """``DEFAULT_DB_PATH`` points at the spec's expected location."""
        assert DEFAULT_DB_PATH == "./data/db/ingestion_history.db"

    def test_db_directory_created_automatically(self, tmp_path):
        """The parent directory should be created if it does not exist."""
        nested = tmp_path / "a" / "b" / "c" / "hist.db"
        checker = SQLiteIntegrityChecker(str(nested))
        checker.mark_success("feedface" * 8, "/tmp/x.pdf")
        assert nested.exists()
        assert nested.parent.is_dir()

    def test_wal_mode_enabled(self, tmp_db):
        """WAL must be active so concurrent readers don't block the writer."""
        checker, db_path = tmp_db
        # Force schema creation by writing something.
        checker.mark_success("a" * 64, "/tmp/x.pdf")
        # Check the journal mode via a fresh connection.
        conn = sqlite3.connect(db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        assert mode.lower() == "wal"

    def test_schema_has_required_columns(self, tmp_db):
        checker, db_path = tmp_db
        checker.mark_success("a" * 64, "/tmp/x.pdf")
        conn = sqlite3.connect(db_path)
        try:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(ingestion_history)")
            }
        finally:
            conn.close()
        expected = {
            "file_hash", "file_path", "file_size", "last_modified",
            "status", "error_msg", "created_at", "updated_at",
        }
        assert expected.issubset(cols)

    def test_file_hash_is_primary_key(self, tmp_db):
        """A second mark for the same hash must overwrite, not duplicate."""
        checker, _ = tmp_db
        checker.mark_success("b" * 64, "/tmp/a.pdf")
        checker.mark_success("b" * 64, "/tmp/a-renamed.pdf")
        record = checker.get_record("b" * 64)
        assert record is not None
        assert record.file_path == "/tmp/a-renamed.pdf"
        assert record.status == "success"


# ---------------------------------------------------------------------------
# get_record
# ---------------------------------------------------------------------------

class TestGetRecord:
    def test_returns_none_for_unknown_hash(self, tmp_db):
        checker, _ = tmp_db
        assert checker.get_record("0" * 64) is None

    def test_round_trips_all_fields(self, tmp_db, sample_file):
        checker, _ = tmp_db
        h = checker.compute_sha256(sample_file)
        size = os.path.getsize(sample_file)
        mtime = os.path.getmtime(sample_file)
        checker.mark_success(
            h, sample_file, file_size=size, last_modified=mtime
        )
        rec = checker.get_record(h)
        assert rec is not None
        assert isinstance(rec, IngestionRecord)
        assert rec.file_hash == h
        assert rec.file_path == sample_file
        assert rec.file_size == size
        assert rec.last_modified == pytest.approx(mtime)
        assert rec.status == "success"
        assert rec.error_msg is None
        assert rec.created_at > 0
        assert rec.updated_at > 0

    def test_records_failure_with_error_msg(self, tmp_db, sample_file):
        checker, _ = tmp_db
        h = checker.compute_sha256(sample_file)
        checker.mark_failed(h, sample_file, error_msg="upstream timeout")
        rec = checker.get_record(h)
        assert rec is not None
        assert rec.status == "failed"
        assert rec.error_msg == "upstream timeout"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    """
    With WAL mode, multiple threads must be able to write concurrently
    without losing rows or hitting 'database is locked'.
    """

    def test_concurrent_marks_all_persist(self, tmp_db):
        checker, _ = tmp_db
        n_threads = 16
        n_per_thread = 25
        barrier = threading.Barrier(n_threads)

        def worker(i: int) -> None:
            barrier.wait()  # maximize contention
            for j in range(n_per_thread):
                h = f"{i:08x}{j:08x}" + "0" * 48
                checker.mark_success(h, f"/tmp/file_{i}_{j}.pdf")

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker, i) for i in range(n_threads)]
            for f in futures:
                f.result()  # surface any exceptions

        # All n_threads * n_per_thread rows must be present.
        conn = sqlite3.connect(checker.db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM ingestion_history"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == n_threads * n_per_thread

    def test_concurrent_mixed_marks_dont_corrupt(self, tmp_db):
        """Mix of success / failed from many threads, no DB corruption."""
        checker, _ = tmp_db
        n_threads = 12
        barrier = threading.Barrier(n_threads)

        def worker(i: int) -> None:
            barrier.wait()
            h = f"{i:016x}" + "0" * 48
            if i % 2 == 0:
                checker.mark_success(h, f"/tmp/{i}.pdf")
            else:
                checker.mark_failed(h, f"/tmp/{i}.pdf", f"err {i}")

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            list(pool.map(worker, range(n_threads)))

        for i in range(n_threads):
            h = f"{i:016x}" + "0" * 48
            rec = checker.get_record(h)
            assert rec is not None
            if i % 2 == 0:
                assert rec.status == "success"
                assert rec.error_msg is None
            else:
                assert rec.status == "failed"
                assert rec.error_msg == f"err {i}"


# ---------------------------------------------------------------------------
# Abstract interface contract
# ---------------------------------------------------------------------------

class TestAbstractInterface:
    """Sanity checks on the abstract base class itself."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            FileIntegrityChecker()  # type: ignore[abstract]

    def test_subclass_must_implement_abstract_methods(self):
        """A subclass that forgets an abstract method must not be instantiable."""

        class HalfBaked(FileIntegrityChecker):
            def compute_sha256(self, path: str) -> str:
                return ""

            # should_skip / mark_success / mark_failed missing on purpose.

        with pytest.raises(TypeError):
            HalfBaked()  # type: ignore[abstract]


# ---------------------------------------------------------------------------

class TestLegacyMigration:
    """A pre-M3 DB (no ``collection`` column) must migrate in place.

    Regression (M4 acceptance): the v1 schema could declare its primary
    key *inline* (``file_hash TEXT PRIMARY KEY``) rather than as a table
    constraint (``PRIMARY KEY (file_hash)``); legacy detection must catch
    both forms or the composite ``collection`` index cannot be created.
    """

    def _create_v1_db(self, db_path, *, inline_pk: bool = True) -> None:
        cols = (
            "file_hash TEXT PRIMARY KEY,\n"
            "file_path TEXT NOT NULL,\n"
            "file_size INTEGER,\n"
            "last_modified REAL,\n"
            "status TEXT NOT NULL CHECK (status IN ('success', 'failed')),\n"
            "error_msg TEXT,\n"
            "created_at REAL NOT NULL,\n"
            "updated_at REAL NOT NULL"
        )
        if not inline_pk:
            cols = (
                "file_hash TEXT NOT NULL,\n"
                "file_path TEXT NOT NULL,\n"
                "file_size INTEGER,\n"
                "last_modified REAL,\n"
                "status TEXT NOT NULL CHECK (status IN ('success', 'failed')),\n"
                "error_msg TEXT,\n"
                "created_at REAL NOT NULL,\n"
                "updated_at REAL NOT NULL,\n"
                "PRIMARY KEY (file_hash)"
            )
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            f"""
            CREATE TABLE ingestion_history (
                {cols}
            )
            """
        )
        conn.execute(
            "INSERT INTO ingestion_history "
            "(file_hash, file_path, status, created_at, updated_at) "
            "VALUES ('legacy-hash', ?, 'success', 1.0, 2.0)",
            ("/legacy.pdf",),
        )
        conn.commit()
        conn.close()

    @pytest.mark.parametrize("inline_pk", [True, False])
    def test_migrates_v1_db_in_place(self, tmp_path, inline_pk: bool) -> None:
        db_path = tmp_path / "legacy.db"
        self._create_v1_db(db_path, inline_pk=inline_pk)

        checker = SQLiteIntegrityChecker(str(db_path))
        # No crash on first use; the v2 composite index exists.
        assert checker.should_skip("legacy-hash") is False

        # Legacy rows are preserved, labelled with the legacy marker.
        rec = checker.get_record("legacy-hash", collection="_default")
        assert rec is not None
        assert rec.status == "success"
        assert rec.collection == "_default"

        # New writes land in the v2 schema under a real collection.
        checker.mark_success("new-hash", "/new.pdf", collection="default")
        assert checker.should_skip("new-hash") is True
        assert checker.get_record("new-hash", collection="default") is not None
