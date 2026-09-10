"""
Cache-consistency tests for the performance pass.

Covers the two reviewer follow-ups that need explicitness:

- A document-UUID *miss* against a fresh cached index must NOT trigger a
  rebuild (so flooding with nonexistent UUIDs can't become a repeated
  O(N) full-integrity scan).
- After the background ingestion worker successfully writes a document's
  integrity record, both the collection-stats cache and the document-UUID
  index are invalidated — so lists refresh and detail pages resolve the
  new document immediately (not after the TTLs).
"""

from __future__ import annotations

import time

from src.application.identifiers import collection_uuid, document_uuid
from src.application.services.document_service import DocumentService
from src.application.services.ingestion_service import IngestionService
from src.core.types import ChunkRecord
from src.ingestion.document_manager import DocumentManager
from src.ingestion.storage.image_storage import ImageStorage
from src.libs.loader.file_integrity import SQLiteIntegrityChecker
from tests.unit.test_document_manager import FakeChromaStore


def _build_manager(tmp_path):
    from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer, SparseEncoder

    integrity = SQLiteIntegrityChecker(str(tmp_path / "integrity.db"))
    integrity.mark_success(
        "hash-old", "/old.pdf", file_size=10, last_modified=1.0,
        collection="default",
    )
    images = ImageStorage(
        db_path=str(tmp_path / "images.db"), base_dir=str(tmp_path / "images"),
    )
    chroma = FakeChromaStore()
    chroma.upsert([
        ChunkRecord(id="old1", text="t", metadata={"source_path": "/old.pdf"}),
    ])
    bm25_dir = tmp_path / "bm25"
    bm25_dir.mkdir(parents=True)
    (bm25_dir / "default.json").write_text(
        '{"n_docs":0,"avgdl":0.0,"k1":1.5,"b":0.75,"terms":{}}',
        encoding="utf-8",
    )
    manager = DocumentManager(
        chroma_store=chroma,
        bm25_indexer=BM25Indexer(
            persist_dir=str(bm25_dir), sparse_encoder=SparseEncoder(),
        ),
        bm25_index=BM25Index(),
        image_storage=images,
        file_integrity=integrity,
    )
    return manager, integrity


class _WritingPipeline:
    """Pipeline whose ``run`` writes a NEW integrity record (no vector writes)."""

    def __init__(self, integrity) -> None:
        self._integrity = integrity

    def run(self, path, *, on_progress=None, trace=None, collection="default", source_path=None):
        self._integrity.mark_success(
            "hash-new", source_path, file_size=123,
            last_modified=2.0, collection=collection,
        )
        return type("Result", (), {"skipped": False})()


def _wait_terminal(ingestion, task_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = ingestion.get_task(task_id)
        if rec is not None and rec.status in ("succeeded", "failed", "skipped"):
            return rec
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not finish in {timeout}s")


class TestUniqueDocumentResolution:
    def test_nonexistent_uuids_build_the_index_once(self, tmp_path) -> None:
        manager, _ = _build_manager(tmp_path)
        svc = DocumentService(manager)

        builds = {"n": 0}
        original = manager.list_document_keys

        def counting(*args, **kwargs):
            builds["n"] += 1
            return original(*args, **kwargs)

        manager.list_document_keys = counting

        # A hit triggers exactly one build.
        existing = document_uuid("default", "/old.pdf")
        assert svc.resolve_document_id(existing) == ("default", "/old.pdf")
        assert builds["n"] == 1

        # Many nonexistent UUIDs against the still-fresh cache: no rebuilds.
        for i in range(5):
            assert svc.resolve_document_id(
                document_uuid("default", f"/missing-{i}.pdf"),
            ) is None
        assert builds["n"] == 1


class TestPostIngestCacheInvalidation:
    def test_new_document_resolves_immediately_after_ingest(self, tmp_path) -> None:
        manager, integrity = _build_manager(tmp_path)
        svc = DocumentService(manager)
        ingestion = IngestionService(
            _WritingPipeline(integrity),
            upload_dir=tmp_path / "uploads",
            on_ingested=svc.invalidate_ingest_caches,
        )

        # Warm both caches against the current state.
        assert svc.resolve_document_id(
            document_uuid("default", "/old.pdf"),
        ) == ("default", "/old.pdf")
        assert svc.get_all_collection_stats_cached(["default"])["default"].n_documents == 1

        # Upload a brand-new document via the background worker path.
        source_path = ingestion.compute_source_path("default", "new.pdf")
        new_uuid = document_uuid("default", str(source_path))
        assert svc.resolve_document_id(new_uuid) is None  # not yet ingested

        rec = ingestion.upload(
            bytes_payload=b"%PDF-1.4",
            filename="new.pdf",
            collection="default",
            collection_id=collection_uuid("default"),
            document_id=new_uuid,
            source_path=source_path,
        )
        ingestion.start_worker(rec.id)
        finished = _wait_terminal(ingestion, rec.id)
        assert finished.status == "succeeded"
        assert source_path.read_bytes() == b"%PDF-1.4"

        # Immediately resolvable (index invalidated by the ingest hook).
        assert svc.resolve_document_id(new_uuid) == ("default", str(source_path))
        # Collection stats reflect the new document right away too.
        assert svc.get_all_collection_stats_cached(["default"])["default"].n_documents == 2
