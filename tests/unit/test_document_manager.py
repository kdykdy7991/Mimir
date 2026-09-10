"""
Unit tests for ``DocumentManager`` (G2).

Uses in-memory fakes for the four backing stores — the
``DocumentManager`` is supposed to be storage-agnostic via
its Protocol-typed constructor, so testing the protocol
contract is the meaningful test. The real-storage contract
tests live alongside the store implementations themselves.
"""

from __future__ import annotations

import json

import pytest

from src.core.types import ChunkRecord
from src.ingestion.document_manager import (
    CollectionStats,
    DeleteResult,
    DocumentDetail,
    DocumentInfo,
    DocumentManager,
)
from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer
from src.ingestion.storage.bm25_indexer import SparseEncoder


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------

class FakeChromaStore:
    """Just enough of ChromaStore for DocumentManager's needs."""

    def __init__(self) -> None:
        # chunk_id -> {"text": str, "metadata": dict}
        self._records: dict[str, dict] = {}
        self.collection_name = "default"

    def upsert(self, records: list[ChunkRecord]) -> int:
        for r in records:
            self._records[r.id] = {
                "text": r.text,
                "metadata": dict(r.metadata),
            }
        return len(records)

    def get_by_metadata(
        self, filters: dict, *, limit: int | None = None,
        collection: str | None = None, **kw,
    ) -> list[dict]:
        if not filters:
            return []
        # ``collection`` is ignored by the fake — it stores every
        # record in a single dict. The real
        # MultiCollectionVectorStore scopes by Chroma collection
        # which the test harness doesn't simulate. Callers that
        # care (M3 contract tests) use the integration test
        # path with the real router.
        _ = collection
        out = []
        for cid, rec in self._records.items():
            if all(rec["metadata"].get(k) == v for k, v in filters.items()):
                out.append({
                    "id": cid,
                    "text": rec["text"],
                    "metadata": rec["metadata"],
                })
        return out

    def delete_by_metadata(
        self, filters: dict, *, collection: str | None = None, **kw,
    ) -> int:
        if not filters:
            return 0
        _ = collection
        keep, drop = [], 0
        for cid, rec in list(self._records.items()):
            if all(rec["metadata"].get(k) == v for k, v in filters.items()):
                del self._records[cid]
                drop += 1
            else:
                keep.append(cid)
        return drop

    def delete(
        self, ids: list[str], *, collection: str | None = None, **kw,
    ) -> int:
        _ = collection
        deleted = 0
        for chunk_id in ids:
            if self._records.pop(chunk_id, None) is not None:
                deleted += 1
        return deleted

    def get_collection_stats(self, **kw) -> dict:
        return {
            "collection_name": self.collection_name,
            "count": len(self._records),
        }


class FakeImageStorage:
    def __init__(self) -> None:
        # doc_hash -> list of records
        self._by_hash: dict[str, list[dict]] = {}
        # image_id -> record (for ``get()``)
        self._by_id: dict[str, dict] = {}

    def save(self, image_id: str, image_bytes: bytes, doc_hash: str) -> None:
        self._by_hash.setdefault(doc_hash, []).append(
            {"id": image_id, "bytes": image_bytes},
        )
        self._by_id[image_id] = {"id": image_id, "bytes": image_bytes}

    def get(self, image_id: str):
        """Look up a single image by id (returns the dict or None).

        Mirrors :meth:`src.ingestion.storage.image_storage.ImageStorage.get`.
        The result is a duck-typed object — callers (e.g.
        DataService) read ``file_path`` from it; the test
        can populate ``file_path`` after ``save`` if needed.
        """
        rec = self._by_id.get(image_id)
        if rec is None:
            return None
        return type("ImageRecord", (), rec)()

    def find_by_doc_hash(
        self, doc_hash: str, *, collection: str | None = None,
        limit: int | None = None,
    ):
        # The fake is single-collection-ish: ``collection`` is accepted
        # for M3 signature parity but not used to filter.
        _ = collection
        return list(self._by_hash.get(doc_hash, []))


class FakeIntegrity:
    """Path-keyed dict standing in for SQLiteIntegrityChecker."""

    def __init__(self) -> None:
        # file_path -> {file_hash, file_size, last_modified, status}
        self._by_path: dict[str, dict] = {}
        # file_hash -> file_path (for forget())
        self._by_hash: dict[str, str] = {}

    def mark_success(
        self, file_hash: str, file_path: str,
        file_size: int = 0, last_modified: float = 0.0,
        collection: str = "default",
    ) -> None:
        # The fake is single-collection-ish: rows are keyed by
        # ``file_path`` only. ``collection`` is recorded on the
        # row dict so M3-aware callers can verify the scoping
        # was forwarded. ``forget`` still keys on ``file_hash``
        # to match legacy behaviour; M3 tests that exercise
        # cross-collection collisions use the real SQLite
        # implementation (tests/unit/test_file_integrity.py).
        self._by_path[file_path] = {
            "file_hash": file_hash,
            "file_path": file_path,
            "file_size": file_size,
            "last_modified": last_modified,
            "status": "success",
            "error_msg": None,
            "created_at": 0.0,
            "updated_at": 0.0,
            "collection": collection,
        }
        self._by_hash[file_hash] = file_path

    def get_record_by_path(
        self, file_path: str, *, collection: str | None = None,
    ):
        rec = self._by_path.get(file_path)
        if rec is None:
            return None
        if collection is not None and rec.get("collection") != collection:
            return None
        return type("R", (), rec)()

    def list_processed(
        self, *, status=None, collection=None, limit=None,
    ):
        rows = list(self._by_path.values())
        if status is not None:
            rows = [r for r in rows if r["status"] == status]
        if collection is not None:
            rows = [r for r in rows if r.get("collection") == collection]
        return [type("R", (), r)() for r in rows]

    def forget(
        self, file_hash: str, *, collection: str = "default",
    ) -> bool:
        # Filter by collection: a row that lives in another
        # collection must NOT be forgotten here.
        path = self._by_hash.get(file_hash)
        if path is None:
            return False
        row = self._by_path.get(path)
        if row is not None and row.get("collection") != collection:
            return False
        self._by_hash.pop(file_hash, None)
        self._by_path.pop(path, None)
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def chroma() -> FakeChromaStore:
    c = FakeChromaStore()
    c.upsert([
        ChunkRecord(id="c1", text="alpha", metadata={"source_path": "/a.pdf", "doc_hash": "hash-a"}),
        ChunkRecord(id="c2", text="beta", metadata={"source_path": "/a.pdf", "doc_hash": "hash-a"}),
        ChunkRecord(id="c3", text="gamma", metadata={"source_path": "/b.pdf", "doc_hash": "hash-b"}),
    ])
    return c


@pytest.fixture
def images() -> FakeImageStorage:
    s = FakeImageStorage()
    s.save("img1", b"a", doc_hash="hash-a")
    s.save("img2", b"b", doc_hash="hash-a")
    s.save("img3", b"c", doc_hash="hash-b")
    return s


@pytest.fixture
def integrity() -> FakeIntegrity:
    f = FakeIntegrity()
    f.mark_success("hash-a", "/a.pdf", file_size=100, last_modified=1.0)
    f.mark_success("hash-b", "/b.pdf", file_size=200, last_modified=2.0)
    return f


@pytest.fixture
def bm25_indexer() -> BM25Indexer:
    """A real BM25Indexer (in-memory, no persistence)."""
    return BM25Indexer(sparse_encoder=SparseEncoder())


@pytest.fixture
def bm25_index(bm25_indexer: BM25Indexer) -> BM25Index:
    """Build a tiny BM25 index from the same chunks as chroma."""
    return bm25_indexer.build([
        ChunkRecord(id="c1", text="alpha beta", metadata={"source_path": "/a.pdf"}),
        ChunkRecord(id="c2", text="beta gamma", metadata={"source_path": "/a.pdf"}),
        ChunkRecord(id="c3", text="gamma delta", metadata={"source_path": "/b.pdf"}),
    ])


@pytest.fixture
def manager(chroma, bm25_indexer, bm25_index, images, integrity):
    return DocumentManager(
        chroma_store=chroma,
        bm25_indexer=bm25_indexer,
        bm25_index=bm25_index,
        image_storage=images,
        file_integrity=integrity,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestListDocuments:
    def test_returns_one_info_per_known_file(self, manager):
        docs = manager.list_documents()
        assert len(docs) == 2
        paths = {d.source_path for d in docs}
        assert paths == {"/a.pdf", "/b.pdf"}

    def test_n_chunks_matches_chroma_state(self, manager):
        docs = {d.source_path: d for d in manager.list_documents()}
        assert docs["/a.pdf"].n_chunks == 2
        assert docs["/b.pdf"].n_chunks == 1

    def test_n_images_matches_image_storage(self, manager):
        docs = {d.source_path: d for d in manager.list_documents()}
        assert docs["/a.pdf"].n_images == 2
        assert docs["/b.pdf"].n_images == 1

    def test_file_size_and_status_carried_through(self, manager):
        docs = {d.source_path: d for d in manager.list_documents()}
        assert docs["/a.pdf"].file_size == 100
        assert docs["/a.pdf"].status == "success"

    def test_empty_integrity_returns_empty_list(self, chroma, bm25_indexer, bm25_index, images):
        manager = DocumentManager(
            chroma_store=chroma,
            bm25_indexer=bm25_indexer,
            bm25_index=bm25_index,
            image_storage=images,
            file_integrity=FakeIntegrity(),
        )
        assert manager.list_documents() == []


class TestGetDocumentDetail:
    def test_returns_detail_with_chunks_and_images(self, manager):
        detail = manager.get_document_detail("/a.pdf")
        assert detail is not None
        assert detail.info.source_path == "/a.pdf"
        assert len(detail.chunks) == 2
        assert len(detail.images) == 2
        # chunks are dicts with id/text/metadata
        ids = {c["id"] for c in detail.chunks}
        assert ids == {"c1", "c2"}

    def test_returns_none_for_unknown_path(self, manager):
        assert manager.get_document_detail("/nope.pdf") is None

    def test_handles_empty_chunks_gracefully(self, manager):
        # Force the chroma lookup to fail by mutating the fake.
        # Easiest path: just ask for a path that has integrity
        # but no chunks. We'll do it by deleting chroma entries
        # for /b.pdf first.
        manager._chroma.delete_by_metadata({"source_path": "/b.pdf"})
        detail = manager.get_document_detail("/b.pdf")
        assert detail is not None
        assert detail.info.n_chunks == 0
        assert detail.info.n_images == 1  # image still there


class TestDeleteDocument:
    def test_deletes_from_chroma(self, manager):
        manager.delete_document("/a.pdf")
        remaining = manager._chroma.get_by_metadata({"source_path": "/a.pdf"})
        assert remaining == []
        # b.pdf chunks untouched
        b_chunks = manager._chroma.get_by_metadata({"source_path": "/b.pdf"})
        assert len(b_chunks) == 1

    def test_doc_hash_fallback_deletes_stale_source_path(self, manager):
        for record in manager._chroma._records.values():
            if record["metadata"].get("doc_hash") == "hash-a":
                record["metadata"]["source_path"] = "/tmp/stale-upload.pdf"
        result = manager.delete_document("/a.pdf")
        assert result.chunks_deleted == 2
        assert manager._chroma.get_by_metadata({"doc_hash": "hash-a"}) == []

    def test_deletes_from_bm25(self, manager):
        # Before: c1 and c2 are in the index.
        assert manager._bm25_index.n_docs == 3
        manager.delete_document("/a.pdf")
        assert manager._bm25_index.n_docs == 1
        # c3 (b.pdf) should still be there.
        remaining = {p.chunk_id for e in manager._bm25_index.terms.values()
                     for p in e.postings}
        assert remaining == {"c3"}

    def test_counts_images(self, manager):
        result = manager.delete_document("/a.pdf")
        assert result.images_deleted == 2

    def test_forgets_integrity(self, manager):
        result = manager.delete_document("/a.pdf")
        assert result.integrity_forgotten is True
        # b.pdf integrity still present
        assert manager._integrity.get_record_by_path("/b.pdf") is not None
        assert manager._integrity.get_record_by_path("/a.pdf") is None

    def test_delete_unknown_path_is_safe(self, manager):
        # No integrity record → result has zero counts, no errors
        result = manager.delete_document("/nope.pdf")
        assert result.chunks_deleted == 0
        assert result.bm25_removed == 0
        assert result.images_deleted == 0
        assert result.integrity_forgotten is False
        assert result.errors == []

    def test_delete_returns_dataclass_with_total(self, manager):
        result = manager.delete_document("/a.pdf")
        assert isinstance(result, DeleteResult)
        assert result.chunks_deleted == 2
        assert result.bm25_removed == 2
        assert result.images_deleted == 2
        # total sums all three counts (NOT integrity_forgotten)
        assert result.total == 6

    def test_keeps_integrity_on_chroma_error(self, manager):
        """A partial delete must remain visible so it can be retried."""
        # Patch the chroma to raise on get_by_metadata.
        original = manager._chroma.get_by_metadata
        manager._chroma.get_by_metadata = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("boom"),
        )
        result = manager.delete_document("/a.pdf")
        assert any("chroma" in e for e in result.errors)
        assert result.integrity_forgotten is False
        assert manager._integrity.get_record_by_path("/a.pdf") is not None


class TestGetCollectionStats:
    def test_counts_distinct_sources(self, manager):
        stats = manager.get_collection_stats()
        assert stats.n_documents == 2
        assert stats.n_chunks == 3
        assert stats.n_images == 3

    def test_collection_name_from_chroma(self, manager):
        stats = manager.get_collection_stats()
        assert stats.collection == "default"

    def test_empty_state(self, chroma, bm25_indexer, bm25_index, images):
        manager = DocumentManager(
            chroma_store=FakeChromaStore(),
            bm25_indexer=bm25_indexer,
            bm25_index=bm25_index,
            image_storage=FakeImageStorage(),
            file_integrity=FakeIntegrity(),
        )
        stats = manager.get_collection_stats()
        assert stats.n_documents == 0
        assert stats.n_chunks == 0
        assert stats.n_images == 0

    def test_to_dict_round_trip(self, manager):
        stats = manager.get_collection_stats()
        d = stats.to_dict()
        assert d == {
            "collection": "default",
            "n_documents": 2,
            "n_chunks": 3,
            "n_images": 3,
        }


class TestGetCorpusOverviewStats:
    def test_avoids_per_document_store_lookups(self, manager):
        manager._chroma.get_by_metadata = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("overview must not load document chunks"),
        )
        manager._images.find_by_doc_hash = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("overview must not load document images"),
        )

        stats = manager.get_corpus_overview_stats(collections=["default"])

        assert stats.n_documents == 2
        assert stats.n_chunks == 3
        assert stats.statuses == ("success", "success")


class TestCollections:
    """M2 batch 1 — collection listing / create / cascade-delete.

    These need a real BM25 ``persist_dir`` (collections are the set of
    index files on disk), so they use a manager whose indexer points at
    ``tmp_path``.
    """

    @pytest.fixture
    def disk_manager(self, chroma, images, integrity, tmp_path):
        bm25_dir = tmp_path / "db" / "bm25"
        bm25_dir.mkdir(parents=True)
        # A valid empty index, matching what ``create_collection``
        # actually persists (not a bare ``{}``).
        (bm25_dir / "default.json").write_text(
            json.dumps({"n_docs": 0, "avgdl": 0.0, "k1": 1.5, "b": 0.75, "terms": {}}),
            encoding="utf-8",
        )
        bm25 = BM25Indexer(
            persist_dir=str(bm25_dir), sparse_encoder=SparseEncoder(),
        )
        return DocumentManager(
            chroma_store=chroma,
            bm25_indexer=bm25,
            bm25_index=BM25Index(),
            image_storage=images,
            file_integrity=integrity,
        )

    def test_list_collections_from_bm25_files(self, disk_manager):
        assert disk_manager.list_collections()[0].name == "default"

    def test_list_collections_empty_persist_dir(self, tmp_path):
        mgr = DocumentManager(
            chroma_store=FakeChromaStore(),
            bm25_indexer=BM25Indexer(
                persist_dir=str(tmp_path / "nowhere"),
                sparse_encoder=SparseEncoder(),
            ),
            bm25_index=BM25Index(),
            image_storage=FakeImageStorage(),
            file_integrity=FakeIntegrity(),
        )
        assert mgr.list_collections() == []

    def test_create_collection_appears_in_listing(self, disk_manager):
        disk_manager.create_collection("reports")
        names = [c.name for c in disk_manager.list_collections()]
        assert "reports" in names

    def test_create_collection_is_idempotent(self, disk_manager):
        disk_manager.create_collection("reports")
        disk_manager.create_collection("reports")  # no error
        assert len(disk_manager.list_collections()) == 2

    def test_delete_collection_cascades_and_removes_marker(self, disk_manager):
        results = disk_manager.delete_collection("default")
        assert results  # at least the docs were deleted
        assert [c.name for c in disk_manager.list_collections()] == []
