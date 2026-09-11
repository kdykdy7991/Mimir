"""
B2.5 — collection document combination filtering.

Covers ``GET /api/v1/collections/{id}/documents`` with ``q`` / ``status`` /
``file_type`` / ``updated_after`` / ``updated_before`` / ``sort`` /
``folder_id`` (incl. ``root``) / repeatable ``tag_id`` (AND), plus the
per-item ``tags`` / ``folder_id`` enrichment and the legacy no-params shape.

Unlike the folder/tag CRUD tests we build a REAL ``SQLiteIntegrityChecker``
seeded with several files of differing ext / size / status / date so the
filters are exercised in SQL (the requirement: never load-then-filter the
whole collection in Python). The chroma / image stores are the same in-memory
fakes used by ``test_document_manager`` / ``test_web_api_perf_pagination``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.application.services.document_service import DocumentService
from src.application.services.web_store import WebApiDB
from src.ingestion.document_manager import DocumentManager
from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer, SparseEncoder
from src.libs.loader.file_integrity import SQLiteIntegrityChecker
from src.web_api.app import create_app
from src.web_api.mappers import collection_uuid, document_uuid
from tests.unit.test_document_manager import FakeChromaStore, FakeImageStorage

COLLECTION = "kb"
COLLECTION_ID = str(collection_uuid(COLLECTION))


# source_path -> (file_hash, ext lower, size, status, iso updated_at)
SEED = {
    "/kb/alpha.pdf":   ("h-alpha",   "pdf", 1000, "success", "2026-09-11T08:00:00Z"),
    "/kb/beta.md":     ("h-beta",    "md",  2000, "success", "2026-09-11T09:00:00Z"),
    "/kb/gamma.pdf":   ("h-gamma",   "pdf", 3000, "failed",  "2026-09-11T10:00:00Z"),
    "/kb/delta.txt":   ("h-delta",   "txt", 4000, "success", "2026-09-11T11:00:00Z"),
    "/kb/epsilon.pdf": ("h-epsilon", "pdf", 5000, "success", "2026-09-11T12:00:00Z"),
}

# Expected filenames under each documented sort.
SORT_EXPECTED = {
    "updated_desc": ["epsilon.pdf", "delta.txt", "gamma.pdf", "beta.md", "alpha.pdf"],
    "updated_asc": ["alpha.pdf", "beta.md", "gamma.pdf", "delta.txt", "epsilon.pdf"],
    "name_asc": ["alpha.pdf", "beta.md", "delta.txt", "epsilon.pdf", "gamma.pdf"],
    "name_desc": ["gamma.pdf", "epsilon.pdf", "delta.txt", "beta.md", "alpha.pdf"],
    "size_desc": ["epsilon.pdf", "delta.txt", "gamma.pdf", "beta.md", "alpha.pdf"],
}


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class _Seed:
    """Holds the fixtures a given tmp test needs to assert on."""

    def __init__(self, tmp_path) -> None:
        integrity_path = str(tmp_path / "integrity.db")
        integrity = SQLiteIntegrityChecker(integrity_path)
        for path, (fhash, _ext, size, status, iso) in SEED.items():
            if status == "success":
                integrity.mark_success(fhash, path, file_size=size, collection=COLLECTION)
            else:
                integrity.mark_failed(fhash, path, "boom", file_size=size, collection=COLLECTION)
        # Overwrite updated_at so the date-range filters are deterministic.
        conn = sqlite3.connect(integrity_path)
        for path, (fhash, _ext, _size, _status, iso) in SEED.items():
            conn.execute(
                "UPDATE ingestion_history SET updated_at = ? "
                "WHERE collection = ? AND file_path = ?",
                (_ts(iso), COLLECTION, path),
            )
        conn.commit()
        conn.close()

        # BM25 dir: collection marker file so ``list_collections`` sees ``kb``.
        bm25_dir = tmp_path / "db" / "bm25"
        bm25_dir.mkdir(parents=True)
        (bm25_dir / f"{COLLECTION}.json").write_text(
            json.dumps({"n_docs": 0, "avgdl": 0.0, "k1": 1.5, "b": 0.75, "terms": {}}),
            encoding="utf-8",
        )
        manager = DocumentManager(
            chroma_store=FakeChromaStore(),
            bm25_indexer=BM25Indexer(
                persist_dir=str(bm25_dir), sparse_encoder=SparseEncoder(),
            ),
            bm25_index=BM25Index(),
            image_storage=FakeImageStorage(),
            file_integrity=integrity,
        )
        self.document = DocumentService(manager)

        self.db = WebApiDB(tmp_path / "db" / "web_api.db")
        self.doc_ids = {
            path: str(document_uuid(COLLECTION, path)) for path in SEED
        }
        # Tags: alpha has A+B; beta has A; gamma has B only.
        self.tag_a = self.db.create_tag(collection_id=COLLECTION_ID, name="alpha-tag", color="blue")
        self.tag_b = self.db.create_tag(collection_id=COLLECTION_ID, name="beta-tag", color="red")
        self.tag_c = self.db.create_tag(collection_id=COLLECTION_ID, name="gamma-tag", color="green")
        self.db.set_document_tags(
            self.doc_ids["/kb/alpha.pdf"],
            [self.tag_a["tag_id"], self.tag_b["tag_id"]],
        )
        self.db.set_document_tags(self.doc_ids["/kb/beta.md"], [self.tag_a["tag_id"]])
        self.db.set_document_tags(self.doc_ids["/kb/gamma.pdf"], [self.tag_b["tag_id"]])

        # Folder "work" holds epsilon (a folder filter); root holds alpha.
        self.folder = self.db.create_folder(collection_id=COLLECTION_ID, name="work")
        self.db.move_document(
            document_id=self.doc_ids["/kb/epsilon.pdf"],
            folder_id=self.folder["folder_id"],
            collection_id=COLLECTION_ID,
        )
        self.db.move_document(
            document_id=self.doc_ids["/kb/alpha.pdf"],
            folder_id=None,
            collection_id=COLLECTION_ID,
        )
        # An empty folder to assert an empty folder filter yields no rows.
        self.empty_folder = self.db.create_folder(collection_id=COLLECTION_ID, name="empty")

        services = ApplicationServices(
            query=object(),
            ingestion=object(),
            document=self.document,
            system=object(),
            trace=None,
            engines=None,
            db=self.db,
        )
        self.client = TestClient(create_app(services=services))
        self.url = f"/api/v1/collections/{COLLECTION_ID}/documents"


def _names(resp) -> list[str]:
    data = resp.json()
    return [item["filename"] for item in data["items"]]


def _ids(resp) -> set[str]:
    data = resp.json()
    return {item["id"] for item in data["items"]}


def test_legacy_no_params_shape(tmp_path) -> None:
    s = _Seed(tmp_path)
    resp = s.client.get(s.url)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["page_info"]["has_more"] is False
    assert len(data["items"]) == 5
    assert all("tags" in item and "folder_id" in item for item in data["items"])
    # status mapping preserved: gamma failed -> "failed", rest "ready".
    by_name = {item["filename"]: item for item in data["items"]}
    assert by_name["gamma.pdf"]["status"] == "failed"
    assert by_name["alpha.pdf"]["status"] == "ready"
    assert by_name["alpha.pdf"]["tags"] != [] and by_name["alpha.pdf"]["folder_id"] is None
    assert [t["name"] for t in by_name["beta.md"]["tags"]] == ["alpha-tag"]
    assert by_name["beta.md"]["folder_id"] is None


def test_stable_pagination_no_dups_or_missing(tmp_path) -> None:
    s = _Seed(tmp_path)
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        resp = s.client.get(s.url, params=params)
        assert resp.status_code == 200
        data = resp.json()
        names = [item["filename"] for item in data["items"]]
        # no duplicates across pages
        assert not (set(seen) & set(names))
        seen += names
        if not data["page_info"]["has_more"]:
            break
        cursor = data["page_info"]["next_cursor"]
        assert cursor is not None
    assert seen == SORT_EXPECTED["updated_desc"]
    assert len(seen) == len(set(seen)) == 5


def test_q_filter_case_insensitive(tmp_path) -> None:
    s = _Seed(tmp_path)
    assert _names(s.client.get(s.url, params={"q": "alpha"})) == ["alpha.pdf"]
    assert _names(s.client.get(s.url, params={"q": "ALPHA"})) == ["alpha.pdf"]
    assert set(_names(s.client.get(s.url, params={"q": "eps"}))) == {"epsilon.pdf"}


def test_status_filter(tmp_path) -> None:
    s = _Seed(tmp_path)
    assert set(_names(s.client.get(s.url, params={"status": "failed"}))) == {"gamma.pdf"}
    assert set(_names(s.client.get(s.url, params={"status": "ready"}))) == {
        "alpha.pdf", "beta.md", "delta.txt", "epsilon.pdf",
    }


def test_file_type_filter(tmp_path) -> None:
    s = _Seed(tmp_path)
    assert set(_names(s.client.get(s.url, params={"file_type": "pdf"}))) == {
        "alpha.pdf", "gamma.pdf", "epsilon.pdf",
    }
    assert _names(s.client.get(s.url, params={"file_type": "MD"})) == ["beta.md"]


def test_date_range_filters(tmp_path) -> None:
    s = _Seed(tmp_path)
    after = s.client.get(s.url, params={"updated_after": "2026-09-11T09:30:00Z"})
    assert set(_names(after)) == {"gamma.pdf", "delta.txt", "epsilon.pdf"}
    before = s.client.get(s.url, params={"updated_before": "2026-09-11T10:30:00Z"})
    assert set(_names(before)) == {"alpha.pdf", "beta.md", "gamma.pdf"}
    both = s.client.get(s.url, params={
        "updated_after": "2026-09-11T08:30:00Z",
        "updated_before": "2026-09-11T10:30:00Z",
    })
    assert set(_names(both)) == {"beta.md", "gamma.pdf"}


def test_each_sort(tmp_path) -> None:
    s = _Seed(tmp_path)
    for sort, expected in SORT_EXPECTED.items():
        resp = s.client.get(s.url, params={"sort": sort})
        assert resp.status_code == 200, resp.text
        assert _names(resp) == expected, f"sort={sort}"


def test_multi_tag_and(tmp_path) -> None:
    s = _Seed(tmp_path)
    # AND: alpha carries both; beta has A only, gamma has B only.
    both = s.client.get(s.url, params={
        "tag_id": [s.tag_a["tag_id"], s.tag_b["tag_id"]],
    })
    assert _names(both) == ["alpha.pdf"]
    # single tag id works too.
    only_a = s.client.get(s.url, params={"tag_id": [s.tag_a["tag_id"]]})
    assert set(_names(only_a)) == {"alpha.pdf", "beta.md"}


def test_folder_filter_incl_root(tmp_path) -> None:
    s = _Seed(tmp_path)
    in_folder = s.client.get(s.url, params={"folder_id": s.folder["folder_id"]})
    assert _names(in_folder) == ["epsilon.pdf"]
    at_root = s.client.get(s.url, params={"folder_id": "root"})
    assert _names(at_root) == ["alpha.pdf"]
    # Empty folder -> no rows, total 0.
    empty = s.client.get(s.url, params={"folder_id": s.empty_folder["folder_id"]})
    assert _names(empty) == []
    assert empty.json()["page_info"]["has_more"] is False


def test_combined_filters(tmp_path) -> None:
    s = _Seed(tmp_path)
    # q=pdf + status=ready => alpha, epsilon (gamma is failed).
    both = s.client.get(s.url, params={"q": "pdf", "status": "ready"})
    assert set(_names(both)) == {"alpha.pdf", "epsilon.pdf"}
    # folder=root + q=pdf => alpha only.
    root_pdf = s.client.get(s.url, params={"folder_id": "root", "q": "pdf"})
    assert _names(root_pdf) == ["alpha.pdf"]
    # tag A + B + folder=root => alpha.
    tag_folder = s.client.get(s.url, params={
        "folder_id": "root",
        "tag_id": [s.tag_a["tag_id"], s.tag_b["tag_id"]],
    })
    assert _names(tag_folder) == ["alpha.pdf"]
    # tag B + folder=work => no match (epsilon has no tags).
    tag_mismatch = s.client.get(s.url, params={
        "folder_id": s.folder["folder_id"],
        "tag_id": [s.tag_b["tag_id"]],
    })
    assert _names(tag_mismatch) == []


def test_empty_result(tmp_path) -> None:
    s = _Seed(tmp_path)
    resp = s.client.get(s.url, params={"q": "no-such-file-xyz"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["page_info"]["has_more"] is False


def test_invalid_date_400(tmp_path) -> None:
    s = _Seed(tmp_path)
    r1 = s.client.get(s.url, params={"updated_after": "not-a-date"})
    assert r1.status_code == 400, r1.text
    r2 = s.client.get(s.url, params={"updated_before": "2026-13-99"})
    assert r2.status_code == 400, r2.text


def test_invalid_sort_400(tmp_path) -> None:
    s = _Seed(tmp_path)
    resp = s.client.get(s.url, params={"sort": "by_magic"})
    assert resp.status_code == 400, resp.text


def test_unknown_folder_404(tmp_path) -> None:
    s = _Seed(tmp_path)
    resp = s.client.get(s.url, params={"folder_id": str(uuid4())})
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "FOLDER_NOT_FOUND"


def test_enrichment_tags_and_folder(tmp_path) -> None:
    s = _Seed(tmp_path)
    resp = s.client.get(s.url)
    assert resp.status_code == 200
    by_id = {item["id"]: item for item in resp.json()["items"]}
    alpha = by_id[s.doc_ids["/kb/alpha.pdf"]]
    assert {t["name"] for t in alpha["tags"]} == {"alpha-tag", "beta-tag"}
    assert {t["id"] for t in alpha["tags"]} == {s.tag_a["tag_id"], s.tag_b["tag_id"]}
    assert {t["color"] for t in alpha["tags"]} == {"blue", "red"}
    assert alpha["folder_id"] is None

    epsilon = by_id[s.doc_ids["/kb/epsilon.pdf"]]
    assert epsilon["tags"] == []
    assert epsilon["folder_id"] == s.folder["folder_id"]

    beta = by_id[s.doc_ids["/kb/beta.md"]]
    assert beta["folder_id"] is None