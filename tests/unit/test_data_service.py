"""
Unit tests for ``DataService`` (G3).

The DataService is a thin wrapper over DocumentManager +
ImageStorage, so we exercise it with in-memory fakes — same
pattern as the G2 tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ingestion.document_manager import (
    CollectionStats,
    DeleteResult,
    DocumentDetail,
    DocumentInfo,
)
from src.ingestion.document_manager import DocumentManager
from src.observability.dashboard.services.data_service import DataService
from tests.unit.test_document_manager import (
    FakeChromaStore,
    FakeImageStorage,
    FakeIntegrity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def images_dir(tmp_path: Path) -> Path:
    d = tmp_path / "images"
    d.mkdir()
    return d


@pytest.fixture
def fake_images(images_dir: Path) -> FakeImageStorage:
    s = FakeImageStorage()
    # Create a real on-disk image so load_image_bytes can read it.
    (images_dir / "img1.png").write_bytes(b"PNGDATA-1")
    (images_dir / "img2.png").write_bytes(b"PNGDATA-2")
    s.save("img1", b"PNGDATA-1", doc_hash="h1")
    s.save("img2", b"PNGDATA-2", doc_hash="h2")
    return s


class _FileImageStorage(FakeImageStorage):
    """
    ImageStorage fake that returns a record with a real
    ``file_path`` so DataService.load_image_bytes can read it.
    """

    def __init__(self, base_dir: Path) -> None:
        super().__init__()
        self._base = base_dir

    def get(self, image_id: str):
        rec = super().get(image_id)
        if rec is None:
            return None
        # Inject a real on-disk path so Path().read_bytes() works.
        rec.file_path = str(self._base / f"{image_id}.png")
        return rec


@pytest.fixture
def fake_images_with_files(images_dir: Path) -> _FileImageStorage:
    s = _FileImageStorage(images_dir)
    (images_dir / "img1.png").write_bytes(b"PNGDATA-1")
    (images_dir / "img2.png").write_bytes(b"PNGDATA-2")
    s.save("img1", b"PNGDATA-1", doc_hash="h1")
    s.save("img2", b"PNGDATA-2", doc_hash="h2")
    return s


@pytest.fixture
def chroma() -> FakeChromaStore:
    c = FakeChromaStore()
    return c


@pytest.fixture
def integrity() -> FakeIntegrity:
    f = FakeIntegrity()
    f.mark_success("h1", "/a.pdf", file_size=100)
    f.mark_success("h2", "/b.pdf", file_size=200)
    return f


@pytest.fixture
def manager(chroma, integrity, fake_images):
    from src.ingestion.storage.bm25_indexer import (
        BM25Index,
        BM25Indexer,
        SparseEncoder,
    )
    bm25_indexer = BM25Indexer(sparse_encoder=SparseEncoder())
    return DocumentManager(
        chroma_store=chroma,
        bm25_indexer=bm25_indexer,
        bm25_index=BM25Index(),
        image_storage=fake_images,
        file_integrity=integrity,
    )


@pytest.fixture
def svc(manager, fake_images_with_files) -> DataService:
    return DataService(manager=manager, images=fake_images_with_files)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestListDocuments:
    def test_delegates_to_manager(self, svc, manager):
        # Populate chroma with chunks for /a.pdf
        manager._chroma.upsert([
            __import__("src.core.types", fromlist=["ChunkRecord"]).ChunkRecord(
                id="c1", text="x", metadata={"source_path": "/a.pdf"},
            ),
        ])
        docs = svc.list_documents()
        assert len(docs) == 2
        assert {d.source_path for d in docs} == {"/a.pdf", "/b.pdf"}


class TestGetCollectionStats:
    def test_delegates_to_manager(self, svc, manager):
        from src.ingestion.document_manager import CollectionStats
        # Just check it returns a CollectionStats; the actual
        # numbers are exercised in test_document_manager.
        stats = svc.get_collection_stats()
        assert isinstance(stats, CollectionStats)
        assert stats.collection == "default"


class TestLoadImage:
    def test_load_image_bytes_returns_raw_bytes(self, svc):
        out = svc.load_image_bytes("img1")
        assert out == b"PNGDATA-1"

    def test_load_image_bytes_returns_none_for_missing(self, svc):
        assert svc.load_image_bytes("nope") is None

    def test_load_image_b64_returns_base64_string(self, svc):
        import base64
        out = svc.load_image_b64("img1")
        assert out == base64.b64encode(b"PNGDATA-1").decode("ascii")

    def test_load_image_b64_returns_none_for_missing(self, svc):
        assert svc.load_image_b64("nope") is None

    def test_load_image_b64_returns_none_on_oserror(
        self, manager, fake_images,
    ):
        # Inject an image_storage whose file_path is a directory
        # so Path().read_bytes() raises.
        class _BadImageStorage(FakeImageStorage):
            def get(self, image_id):
                rec = super().get(image_id)
                if rec is not None:
                    # Point at a non-existent path that the OS
                    # can't read.
                    rec.file_path = "/nonexistent/path/img.png"
                return rec

        bad = _BadImageStorage()
        bad.save("img1", b"x", doc_hash="h1")
        svc = DataService(manager=manager, images=bad)
        assert svc.load_image_bytes("img1") is None
        assert svc.load_image_b64("img1") is None
