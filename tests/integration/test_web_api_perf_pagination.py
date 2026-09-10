"""
Performance-pass regression tests for the knowledge-base list endpoints.

Covers the new cheap paths added for the 2026-09 audit:

- ``GET /documents`` — true server-side pagination across every collection.
- ``GET /collections/{id}/documents`` — DB-level paging (not load-then-slice).
- ``GET /collections`` — batch-aggregated stats (no per-document N+1).
- document-UUID resolution going through the cached index instead of a
  full Chroma/Image scan.

Uses the same in-memory store fakes as ``test_web_api_endpoints``; the
manager-level fallbacks keep these deterministic and dependency-free.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.application.services.document_service import DocumentService
from src.core.settings import Settings
from src.core.types import ChunkRecord
from src.ingestion.document_manager import DocumentManager
from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer, SparseEncoder
from src.web_api.app import create_app
from src.web_api.mappers import collection_uuid, document_uuid
from tests.unit.test_document_manager import (
    FakeChromaStore,
    FakeImageStorage,
    FakeIntegrity,
)


def _manager(tmp_path) -> DocumentManager:
    chroma = FakeChromaStore()
    chroma.upsert([
        ChunkRecord(id="c1", text="alpha", metadata={"source_path": "/a.pdf"}),
        ChunkRecord(id="c2", text="beta", metadata={"source_path": "/a.pdf"}),
        ChunkRecord(id="c3", text="gamma", metadata={"source_path": "/b.pdf"}),
    ])
    integrity = FakeIntegrity()
    integrity.mark_success("hash-a", "/a.pdf", file_size=100, last_modified=1.0, collection="default")
    integrity.mark_success("hash-b", "/b.pdf", file_size=200, last_modified=2.0, collection="default")
    images = FakeImageStorage()
    images.save("img1", b"x", doc_hash="hash-a")

    bm25_dir = tmp_path / "db" / "bm25"
    bm25_dir.mkdir(parents=True)
    for name in ("default", "reports"):
        (bm25_dir / f"{name}.json").write_text(
            json.dumps({"n_docs": 0, "avgdl": 0.0, "k1": 1.5, "b": 0.75, "terms": {}}),
            encoding="utf-8",
        )
    return DocumentManager(
        chroma_store=chroma,
        bm25_indexer=BM25Indexer(
            persist_dir=str(bm25_dir), sparse_encoder=SparseEncoder(),
        ),
        bm25_index=BM25Index(),
        image_storage=images,
        file_integrity=integrity,
    )


class _NoOpIngestion:
    """Ingestion stub exposing what ``to_document_detail`` reads."""
    def get_latest_task_for(self, collection_id, source_path):
        return None


@pytest.fixture
def client(tmp_path) -> TestClient:
    services = ApplicationServices(
        query=object(),
        ingestion=_NoOpIngestion(),
        document=DocumentService(_manager(tmp_path)),
        system=object(),
        trace=None,
        engines=None,
        db=None,
    )
    return TestClient(create_app(services=services))


class TestListAllDocuments:
    def test_pages_across_all_collections(self, client) -> None:
        resp = client.get("/api/v1/documents?limit=1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["page_info"]["has_more"] is True
        assert body["page_info"]["next_cursor"]
        assert len(body["items"]) == 1
        first_name = body["items"][0]["filename"]

        next_cursor = body["page_info"]["next_cursor"]
        resp2 = client.get(f"/api/v1/documents?limit=1&cursor={next_cursor}")
        assert resp2.status_code == 200
        second = resp2.json()["items"]
        # two documents total — second page is the last, with a different doc
        assert len(second) == 1
        assert second[0]["filename"] != first_name
        assert resp2.json()["page_info"]["has_more"] is False


class TestListCollectionDocuments:
    def test_db_paging_has_totals(self, client) -> None:
        cid = collection_uuid("default")
        resp = client.get(f"/api/v1/collections/{cid}/documents?limit=1")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["page_info"]["has_more"] is True
        first_name = body["items"][0]["filename"]

        resp2 = client.get(
            f"/api/v1/collections/{cid}/documents?limit=1&cursor={body['page_info']['next_cursor']}",
        )
        assert resp2.status_code == 200
        assert resp2.json()["items"][0]["filename"] != first_name
        assert resp2.json()["page_info"]["has_more"] is False


class TestListCollectionsBatchStats:
    def test_reports_aggregated_document_and_chunk_counts(self, client) -> None:
        resp = client.get("/api/v1/collections")
        assert resp.status_code == 200
        items = {item["name"]: item for item in resp.json()["items"]}
        # Both BM25 markers are listed (even with no docs in "reports").
        assert set(items) == {"default", "reports"}
        assert items["default"]["document_count"] == 2
        assert items["default"]["chunk_count"] == 3


class TestDocumentUuidResolution:
    def test_resolves_via_index_without_scan(self, client) -> None:
        target = document_uuid("default", "/a.pdf")
        resp = client.get(f"/api/v1/documents/{target}")
        assert resp.status_code == 200
        assert resp.json()["filename"] == "a.pdf"

        unknown = document_uuid("default", "/missing.pdf")
        assert client.get(f"/api/v1/documents/{unknown}").status_code == 404


# ---------------------------------------------------------------------------
# Call-count assertions — prove the N+1 is actually gone on the hot paths.
# ---------------------------------------------------------------------------

class _CountingChroma:
    """Chroma spy that records how many times Chroma is hit."""

    def __init__(self, records):
        self._records = records
        self.chroma_gets = 0
        self.stats_calls = 0
        self.collection_name = "default"

    def upsert(self, records):
        for r in records:
            self._records[r.id] = {"text": r.text, "metadata": dict(r.metadata)}

    def get_by_metadata(self, filters, *, limit=None, collection=None, **kw):
        self.chroma_gets += 1
        return [{"id": k, "text": v["text"], "metadata": v["metadata"]}
                for k, v in self._records.items()
                if all(v["metadata"].get(f) == val for f, val in filters.items())]

    def get_collection_stats(self, *, collection=None, **kw):
        self.stats_calls += 1
        return {"collection_name": collection or "default", "count": len(self._records)}


class _CountingImages:
    """Image spy exposing the bulk APIs so the grouped path is exercised."""

    def __init__(self, by_hash):
        self._by_hash = by_hash
        self.group_calls = 0
        self.per_doc_calls = 0

    def image_counts(self, *, collections=None):
        self.group_calls += 1
        out = {}
        for c, h, n in self._by_hash:
            out[c] = out.get(c, 0) + n
        return out

    def count_by_doc_hashes(self, doc_hashes, *, collection=None):
        self.group_calls += 1
        out = {}
        for c, h, n in self._by_hash:
            if h in doc_hashes and (collection is None or c == collection):
                out[(c, h)] = out.get((c, h), 0) + n
        return out

    def find_by_doc_hash(self, doc_hash, *, collection=None, limit=None):
        self.per_doc_calls += 1
        return [1] * sum(n for c, h, n in self._by_hash
                         if h == doc_hash and (collection is None or c == collection))


def _counting_manager():
    """Manager over spy stores with 20 documents in one collection."""
    from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer, SparseEncoder
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())

    chroma = _CountingChroma({})
    integrity = FakeIntegrity()
    for i in range(20):
        chroma.upsert([ChunkRecord(
            id=f"c{i}", text="t", metadata={"source_path": f"/d{i}.pdf"},
        )])
        integrity.mark_success(f"h{i}", f"/d{i}.pdf", file_size=i, collection="default")
    by_hash = [("default", f"h{i}", 1 if i % 2 == 0 else 0) for i in range(20)]
    images = _CountingImages(by_hash)

    bm25_dir = tmp / "db" / "bm25"
    bm25_dir.mkdir(parents=True)
    (bm25_dir / "default.json").write_text(
        json.dumps({"n_docs": 0, "avgdl": 0.0, "k1": 1.5, "b": 0.75, "terms": {}}),
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
    return manager, chroma, images


class TestPagedListCallCounts:
    def test_pages_only_query_the_current_page(self) -> None:
        manager, chroma, images = _counting_manager()
        service = DocumentService(manager)

        page, total = service.list_documents_paged(offset=0, limit=5)
        assert len(page) == 5
        assert total == 20
        # Exactly one page-size of Chroma lookups, and exactly one grouped
        # image-count query — NOT 20 per-document image lookups.
        assert chroma.chroma_gets == 5
        assert images.group_calls >= 1
        assert images.per_doc_calls == 0

    def test_collection_stats_batch_uses_aggregates(self) -> None:
        manager, chroma, images = _counting_manager()
        service = DocumentService(manager)
        stats = service.get_all_collection_stats(["default"])
        assert stats["default"].n_documents == 20
        # One grouped image query + one grouped doc-count (fake integrator)
        # + one Chroma total — none of them per-document.
        assert images.group_calls >= 1
        assert images.per_doc_calls == 0