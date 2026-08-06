"""
BM25 data-consistency closed loop (M3 收尾).

Drives the **real** stack through the Web API — real pipeline (BM25
``load + add + atomic save``), real ``DocumentManager.delete`` (BM25
``load + remove + save``), real ``EngineCache`` with per-collection
``SparseRetriever`` caching — and proves the sparse path stays in sync
with the on-disk index across uploads / deletes / cache invalidation.

Covered scenarios:
1. 连续上传 A/B → 两个文档都可 sparse 检索（旧行为每次 rebuild 会丢 A）
2. 删除 A 后 → 仅 B 可 sparse 检索
3. SparseRetriever 缓存已预热后上传 C → C 仍可查（缓存失效生效）
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices, build_application_services
from src.application.identifiers import collection_uuid, document_uuid
from src.core.settings import Settings, VectorStoreSettings
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.vector_store.vector_store_factory import VectorStoreFactory
from src.web_api.app import create_app


class _FakeEmbedding(BaseEmbedding):
    """Deterministic dense embeddings — vector contents don't matter."""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    @property
    def dimensions(self) -> int:
        return self._dim

    def embed(self, texts, **kwargs):
        return [
            [float((hash(t) % 100) + i) / 100 for i in range(self._dim)]
            for t in texts
        ]


def _make_pdf(path: str, text: str) -> None:
    """A tiny one-page PDF whose only content is ``text``."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    doc.save(path)
    doc.close()


@pytest.fixture
def services(tmp_path) -> ApplicationServices:
    store = VectorStoreFactory.create_multi_collection(
        VectorStoreSettings(
            backend="chroma",
            persist_path=str(tmp_path / "db" / "chroma"),
        ),
    )
    return build_application_services(
        data_dir=str(tmp_path),
        settings=Settings(),
        embedding=_FakeEmbedding(),
        vector_store=store,
    )


@pytest.fixture
def client(services: ApplicationServices) -> TestClient:
    return TestClient(create_app(services=services))


def _ensure_default_collection(client: TestClient) -> str:
    items = client.get("/api/v1/collections").json()["items"]
    existing = next((c for c in items if c["name"] == "default"), None)
    if existing is not None:
        return existing["id"]
    resp = client.post("/api/v1/collections", json={"name": "default"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _upload_and_wait(
    client: TestClient, cid: str, filename: str, text: str,
) -> tuple[str, str]:
    """Upload a PDF with the given text; poll the task; return (task_id, doc_id)."""
    pdf = f"/tmp/bm25-{filename}"
    _make_pdf(pdf, text)
    with open(pdf, "rb") as fh:
        resp = client.post(
            f"/api/v1/collections/{cid}/documents",
            files={"file": (filename, fh, "application/pdf")},
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    task_id = body["task_id"]
    doc_id = body["document"]["id"]

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] in ("succeeded", "failed"):
            assert task["status"] == "succeeded", task
            return task_id, doc_id
        time.sleep(0.1)
    raise AssertionError(f"upload task {task_id} did not finish")


def _sparse_doc_ids(client: TestClient, cid: str, term: str) -> set[str]:
    resp = client.post(
        f"/api/v1/collections/{cid}/queries",
        json={"query": term, "top_k": 10, "mode": "sparse"},
    )
    assert resp.status_code == 200, resp.text
    return {c["document_id"] for c in resp.json()["citations"]}


class TestBm25Consistency:
    def test_consecutive_uploads_both_sparse_retrievable(
        self, client: TestClient,
    ) -> None:
        cid = _ensure_default_collection(client)
        _, doc_a = _upload_and_wait(client, cid, "a.pdf", "alpha rocket launch")
        _, doc_b = _upload_and_wait(client, cid, "b.pdf", "beta comet orbit")

        # Both documents are sparse-retrievable after consecutive uploads
        # (the second upload must add() to the existing index, not rebuild).
        assert _sparse_doc_ids(client, cid, "alpha") == {doc_a}
        assert _sparse_doc_ids(client, cid, "beta") == {doc_b}

    def test_delete_a_leaves_only_b(self, client: TestClient) -> None:
        cid = _ensure_default_collection(client)
        _, doc_a = _upload_and_wait(client, cid, "a.pdf", "alpha rocket launch")
        _, doc_b = _upload_and_wait(client, cid, "b.pdf", "beta comet orbit")

        resp = client.delete(f"/api/v1/documents/{doc_a}")
        assert resp.status_code == 204, resp.text

        # A is gone from sparse; B survives.
        assert _sparse_doc_ids(client, cid, "alpha") == set()
        assert _sparse_doc_ids(client, cid, "beta") == {doc_b}

    def test_upload_after_warm_cache_is_queryable(self, client: TestClient) -> None:
        cid = _ensure_default_collection(client)
        _, doc_a = _upload_and_wait(client, cid, "a.pdf", "alpha rocket launch")

        # Warm the SparseRetriever cache with a query.
        assert _sparse_doc_ids(client, cid, "alpha") == {doc_a}

        # Upload C AFTER the cache is warm — the upload must invalidate the
        # collection's cached engine so C is immediately sparse-retrievable.
        _, doc_c = _upload_and_wait(client, cid, "c.pdf", "gamma solar flare")
        assert _sparse_doc_ids(client, cid, "gamma") == {doc_c}
        # And the previously-warm alpha query still sees A.
        assert _sparse_doc_ids(client, cid, "alpha") == {doc_a}


# ---------------------------------------------------------------------------
# Services-level helpers (concurrency tests drive the real worker threads)
# ---------------------------------------------------------------------------

def _pdf_bytes(text: str) -> bytes:
    pdf = "/tmp/bm25-conc.pdf"
    _make_pdf(pdf, text)
    return Path(pdf).read_bytes()


def _wait_service_task(services, task_id, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = services.ingestion.get_task(task_id)
        if rec is not None and rec.status in ("succeeded", "failed"):
            assert rec.status == "succeeded", rec
            return
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish")


def _upload_via_services(services, collection: str, filename: str, text: str):
    """Upload through the real IngestionService worker; returns (doc_id, source_path)."""
    source_path = services.ingestion.compute_source_path(collection, filename)
    doc_id = document_uuid(collection, str(source_path))
    rec = services.ingestion.upload(
        bytes_payload=_pdf_bytes(text),
        filename=filename,
        collection=collection,
        collection_id=collection_uuid(collection),
        document_id=doc_id,
        source_path=source_path,
    )
    _wait_service_task(services, rec.id)
    return doc_id, str(source_path)


def _sparse_doc_ids_via_services(services, collection: str, term: str) -> set[str]:
    result = services.query.search(
        term, mode="sparse", top_k=10, collection=collection,
    )
    return {
        document_uuid(collection, c.metadata.get("source_path", ""))
        for c in result.chunks
    }


# ---------------------------------------------------------------------------
# Concurrency (M3 收尾): per-collection write lock + unique temp files
# ---------------------------------------------------------------------------

class TestBm25Concurrency:
    def test_concurrent_uploads_same_collection_all_retrievable(
        self, services: ApplicationServices, monkeypatch,
    ) -> None:
        from src.ingestion.storage.bm25_indexer import BM25Indexer

        # Widen the load→save window so the concurrent workers actually
        # overlap their read-modify-write (real files save too fast to
        # reliably reproduce the race). With the per-collection write
        # lock the saves serialize; without it, updates are lost.
        real_save = BM25Indexer.save

        def _slow_save(self, index, name="corpus", trace=None):
            time.sleep(0.05)
            return real_save(self, index, name, trace)

        monkeypatch.setattr(BM25Indexer, "save", _slow_save)

        services.document.create_collection("default")
        # Baseline doc A first, so the on-disk index already exists.
        a_id, _ = _upload_via_services(services, "default", "a.pdf", "alpha rocket")

        docs = ["beta", "gamma", "delta"]
        with ThreadPoolExecutor(max_workers=len(docs)) as ex:
            futures = {
                term: ex.submit(
                    _upload_via_services, services, "default",
                    f"{term}.pdf", f"{term} payload",
                )
                for term in docs
            }
            doc_ids = {term: futures[term].result()[0] for term in docs}

        # Every concurrent upload survived the read-modify-write race.
        assert _sparse_doc_ids_via_services(services, "default", "alpha") == {a_id}
        for term in docs:
            assert _sparse_doc_ids_via_services(services, "default", term) == {
                doc_ids[term],
            }, f"lost update: {term} not retrievable after concurrent uploads"

    def test_upload_delete_race_keeps_index_consistent(
        self, services: ApplicationServices, monkeypatch,
    ) -> None:
        from src.ingestion.storage.bm25_indexer import BM25Indexer

        # Widen the load→save window so the upload and delete actually
        # interleave (otherwise real saves are too fast to reproduce the
        # "upload resurrects a concurrently-deleted doc" race).
        real_save = BM25Indexer.save

        def _slow_save(self, index, name="corpus", trace=None):
            time.sleep(0.05)
            return real_save(self, index, name, trace)

        monkeypatch.setattr(BM25Indexer, "save", _slow_save)

        services.document.create_collection("default")
        a_id, a_path = _upload_via_services(services, "default", "a.pdf", "alpha rocket")
        b_id, _ = _upload_via_services(services, "default", "b.pdf", "beta comet")

        with ThreadPoolExecutor(max_workers=2) as ex:
            f_upload = ex.submit(
                _upload_via_services, services, "default", "c.pdf", "gamma solar",
            )
            f_delete = ex.submit(
                lambda: services.document.delete_document(a_path, collection="default"),
            )
            c_id, _ = f_upload.result()
            f_delete.result()

        # Regardless of interleaving, A is gone and B + C survive (the lock
        # prevents the "upload resurrects a concurrently-deleted doc" race).
        assert _sparse_doc_ids_via_services(services, "default", "alpha") == set()
        assert _sparse_doc_ids_via_services(services, "default", "beta") == {b_id}
        assert _sparse_doc_ids_via_services(services, "default", "gamma") == {c_id}

    def test_parallel_uploads_different_collections_do_not_block(
        self, services: ApplicationServices,
    ) -> None:
        services.document.create_collection("alpha_coll")
        services.document.create_collection("beta_coll")

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(
                _upload_via_services, services, "alpha_coll", "a.pdf", "alpha rocket",
            )
            f2 = ex.submit(
                _upload_via_services, services, "beta_coll", "b.pdf", "beta comet",
            )
            a_id, _ = f1.result()
            b_id, _ = f2.result()

        # Per-collection locks mean the two collections proceed in parallel.
        assert _sparse_doc_ids_via_services(services, "alpha_coll", "alpha") == {a_id}
        assert _sparse_doc_ids_via_services(services, "beta_coll", "beta") == {b_id}

    def test_restart_preserves_on_disk_index(self, tmp_path) -> None:
        def _build() -> ApplicationServices:
            store = VectorStoreFactory.create_multi_collection(
                VectorStoreSettings(
                    backend="chroma",
                    persist_path=str(tmp_path / "db" / "chroma"),
                ),
            )
            return build_application_services(
                data_dir=str(tmp_path),
                settings=Settings(),
                embedding=_FakeEmbedding(),
                vector_store=store,
            )

        s1 = _build()
        s1.document.create_collection("default")
        _, a_path = _upload_via_services(s1, "default", "a.pdf", "alpha rocket")
        b_id, _ = _upload_via_services(s1, "default", "b.pdf", "beta comet")
        s1.document.delete_document(a_path, collection="default")

        # "Restart": a brand-new services stack on the same data_dir.
        s2 = _build()
        assert _sparse_doc_ids_via_services(s2, "default", "beta") == {b_id}
        assert _sparse_doc_ids_via_services(s2, "default", "alpha") == set()


__all__ = []
