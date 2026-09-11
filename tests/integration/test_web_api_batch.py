"""
B2.6–B2.9 — batch document operations.

Coverage: normal operation, partial success (one bad item doesn't stop
others), >100 items rejected, unknown/foreign doc or tag rejected, empty
batch, reprocess duplicate/idempotency, delete idempotency.

Uses the stub-document pattern with a tmp_path WebApiDB so no real RAG
store is touched. The batch router is mounted explicitly in the test
helper because app integration is wired by the coordinator.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.application.services.task_tracker import TaskTracker
from src.application.services.web_store import WebApiDB
from src.web_api.app import API_PREFIX, create_app
from src.web_api.mappers import collection_uuid
from src.web_api.routers import batch

COLLECTION = "kb"
COLLECTION_ID = collection_uuid(COLLECTION)
OTHER_COLLECTION_ID = collection_uuid("elsewhere")

DOC_A = "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f"
DOC_B = "8e5b2d4f-3c1b-5e3d-9f0a-2b3c4d5e6f70"
UNKNOWN_DOC = "99999999-0000-4000-8000-000000000000"
FOREIGN_DOC = "12345678-0000-4000-8000-000000000000"

PATH_A = f"/data/uploads/{COLLECTION}/a.pdf"
PATH_B = f"/data/uploads/{COLLECTION}/b.pdf"


class _StubDoc:
    """Minimal DocumentService fake: resolves document ids to a collection."""

    def __init__(self, doc_map=None, delete_errors=False) -> None:
        if doc_map is None:
            self.map = {
                str(DOC_A): (COLLECTION, PATH_A),
                str(DOC_B): (COLLECTION, PATH_B),
            }
        else:
            self.map = dict(doc_map)
        self.delete_calls = []
        self.delete_errors = delete_errors

    def resolve_document_id(self, document_id):
        return self.map.get(str(document_id))

    def list_collections(self):
        ref = type("R", (), {})
        ref.name = COLLECTION
        return [ref]

    def delete_document(self, source_path, collection):
        self.delete_calls.append((source_path, collection))
        return type("DeleteResult", (), {
            "errors": [f"partial failure {source_path}"] if self.delete_errors else [],
            "chunks_deleted": 3 if not self.delete_errors else 1,
            "bm25_removed": not self.delete_errors,
        })()


class _TrackerIngestion:
    """Ingestion fake exposing a real TaskTracker for reprocess tests."""

    def __init__(self, db: WebApiDB) -> None:
        self.tracker = TaskTracker(db=db)
        self.reprocess_calls = []

    def reprocess(self, **kwargs):
        self.reprocess_calls.append(kwargs)
        return self.tracker.create(
            task_id=uuid4(),
            document_id=kwargs["document_id"],
            collection_id=kwargs["collection_id"],
            source_path=kwargs["source_path"],
            filename=kwargs["filename"],
        )


def _build_app(services):
    app = create_app(services=services)
    app.include_router(batch.router, prefix=API_PREFIX)
    return app


def _client(tmp_path, stub=None, with_reprocess=False) -> tuple[TestClient, WebApiDB, _StubDoc, object]:
    stub = stub or _StubDoc()
    db = WebApiDB(tmp_path / "db" / "web_api.db")
    ingestion = _TrackerIngestion(db) if with_reprocess else object()
    services = ApplicationServices(
        query=object(), ingestion=ingestion, document=stub,
        system=object(), trace=object(), engines=object(), db=db,
    )
    return TestClient(_build_app(services)), db, stub, ingestion


@pytest.fixture(autouse=True)
def _reset_reprocess_state():
    """Isolate the module-level reprocess bookkeeping across tests."""
    from src.web_api.routers import batch as batch_mod
    batch_mod._REPROCESS_IN_FLIGHT.clear()
    batch_mod._REPROCESS_BY_KEY.clear()
    yield


def _items(resp):
    return resp.json()["items"]


# ---------------------------------------------------------------------------
# B2.6 — batch tags
# ---------------------------------------------------------------------------

def test_batch_tags_add_ok(tmp_path):
    client, db, _, _ = _client(tmp_path)
    t1 = db.create_tag(collection_id=str(COLLECTION_ID), name="财务")["tag_id"]
    t2 = db.create_tag(collection_id=str(COLLECTION_ID), name="研发")["tag_id"]

    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/tags",
        json={"action": "add", "tag_ids": [t1, t2], "document_ids": [DOC_A, DOC_B]},
    )
    assert resp.status_code == 200, resp.text
    assert all(it["status"] == "success" for it in _items(resp))
    assert set(db.document_tag_ids(str(DOC_A))) == {t1, t2}
    assert set(db.document_tag_ids(str(DOC_B))) == {t1, t2}


def test_batch_tags_remove_ok(tmp_path):
    client, db, _1, _2 = _client(tmp_path)
    t1 = db.create_tag(collection_id=str(COLLECTION_ID), name="保留")["tag_id"]
    t2 = db.create_tag(collection_id=str(COLLECTION_ID), name="移除")["tag_id"]
    db.set_document_tags(str(DOC_A), [t1, t2])

    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/tags",
        json={"action": "remove", "tag_ids": [t2], "document_ids": [DOC_A]},
    )
    assert resp.status_code == 200, resp.text
    assert db.document_tag_ids(str(DOC_A)) == [t1]


def test_batch_tags_replace_ok(tmp_path):
    client, db, _1, _2 = _client(tmp_path)
    t1 = db.create_tag(collection_id=str(COLLECTION_ID), name="a")["tag_id"]
    t2 = db.create_tag(collection_id=str(COLLECTION_ID), name="b")["tag_id"]
    db.set_document_tags(str(DOC_A), [t1])

    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/tags",
        json={"action": "replace", "tag_ids": [t2], "document_ids": [DOC_A]},
    )
    assert resp.status_code == 200
    assert db.document_tag_ids(str(DOC_A)) == [t2]


def test_batch_tags_partial_success_one_bad_doc(tmp_path):
    client, db, _1, _2 = _client(tmp_path)
    t1 = db.create_tag(collection_id=str(COLLECTION_ID), name="x")["tag_id"]
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/tags",
        json={"action": "add", "tag_ids": [t1], "document_ids": [DOC_A, UNKNOWN_DOC]},
    )
    assert resp.status_code == 200, resp.text
    items = {it["document_id"]: it for it in _items(resp)}
    assert items[str(DOC_A)]["status"] == "success"
    assert items[str(UNKNOWN_DOC)]["status"] == "error"
    assert items[str(UNKNOWN_DOC)]["error"]["code"] == "DOCUMENT_NOT_FOUND"
    # the valid doc was still tagged
    assert db.document_tag_ids(str(DOC_A)) == [t1]


def test_batch_tags_foreign_doc_rejected(tmp_path):
    stub = _StubDoc({FOREIGN_DOC: ("elsewhere", "/f.pdf")})
    client, db, _1, _2 = _client(tmp_path, stub=stub)
    t1 = db.create_tag(collection_id=str(COLLECTION_ID), name="x")["tag_id"]
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/tags",
        json={"action": "add", "tag_ids": [t1], "document_ids": [FOREIGN_DOC]},
    )
    assert resp.status_code == 200
    assert _items(resp)[0]["status"] == "error"
    assert _items(resp)[0]["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_batch_tags_foreign_tag_rejected(tmp_path):
    client, db, _1, _2 = _client(tmp_path)
    foreign_tag = db.create_tag(collection_id=str(OTHER_COLLECTION_ID), name="away")["tag_id"]
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/tags",
        json={"action": "add", "tag_ids": [foreign_tag], "document_ids": [DOC_A]},
    )
    assert resp.status_code == 200, resp.text
    item = _items(resp)[0]
    assert item["status"] == "error"
    assert item["error"]["code"] == "TAG_NOT_FOUND"


def test_batch_tags_unknown_collection_404(tmp_path):
    client, _1, _2, _3 = _client(tmp_path)
    resp = client.post(
        f"/api/v1/collections/{OTHER_COLLECTION_ID}/documents/batch/tags",
        json={"action": "add", "tag_ids": [], "document_ids": [DOC_A]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "COLLECTION_NOT_FOUND"


# ---------------------------------------------------------------------------
# B2.7 — batch move
# ---------------------------------------------------------------------------

def test_batch_move_to_folder_and_root(tmp_path):
    client, db, _1, _2 = _client(tmp_path)
    fid = db.create_folder(collection_id=str(COLLECTION_ID), name="工作")["folder_id"]

    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/move",
        json={"folder_id": fid, "document_ids": [DOC_A, DOC_B]},
    )
    assert resp.status_code == 200
    assert all(it["status"] == "success" for it in _items(resp))
    assert db.document_placement(str(DOC_A))["folder_id"] == fid
    assert db.document_placement(str(DOC_B))["folder_id"] == fid

    resp_root = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/move",
        json={"folder_id": None, "document_ids": [DOC_A]},
    )
    assert resp_root.status_code == 200
    assert db.document_placement(str(DOC_A))["folder_id"] is None


def test_batch_move_partial_and_foreign_folder(tmp_path):
    client, db, _1, _2 = _client(tmp_path)
    foreign = db.create_folder(collection_id=str(OTHER_COLLECTION_ID), name="else")["folder_id"]

    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/move",
        json={"folder_id": foreign, "document_ids": [DOC_A, UNKNOWN_DOC]},
    )
    assert resp.status_code == 200, resp.text
    items = {it["document_id"]: it for it in _items(resp)}
    assert items[str(DOC_A)]["status"] == "error"
    assert items[str(DOC_A)]["error"]["code"] == "FOLDER_NOT_FOUND"
    assert items[str(UNKNOWN_DOC)]["status"] == "error"
    assert items[str(UNKNOWN_DOC)]["error"]["code"] == "DOCUMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# B2.8 — batch reprocess
# ---------------------------------------------------------------------------

def test_batch_reprocess_success_task_ids(tmp_path):
    client, _1, _2, ingestion = _client(tmp_path, with_reprocess=True)
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/reprocess",
        json={"document_ids": [DOC_A, DOC_B]},
    )
    assert resp.status_code == 200, resp.text
    items = _items(resp)
    assert all(it["status"] == "success" for it in items)
    assert len({it["task_id"] for it in items}) == 2
    assert len(ingestion.reprocess_calls) == 2


def test_batch_reprocess_duplicate_blocked_without_key(tmp_path):
    client, _1, _2, _ing = _client(tmp_path, with_reprocess=True)
    resp1 = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/reprocess",
        json={"document_ids": [DOC_A]},
    )
    assert resp1.json()["items"][0]["status"] == "success"
    first_task = resp1.json()["items"][0]["task_id"]

    resp2 = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/reprocess",
        json={"document_ids": [DOC_A]},
    )
    item = resp2.json()["items"][0]
    assert item["status"] == "error"
    assert item["error"]["code"] == "DUPLICATE_REPROCESS"
    assert item["error"]["details"]["existing_task_id"] == first_task


def test_batch_reprocess_idempotency_via_key(tmp_path):
    client, _1, _2, ingestion = _client(tmp_path, with_reprocess=True)
    headers = {"Idempotency-Key": "repro-abc-123"}
    body = {"document_ids": [DOC_A, DOC_B]}

    resp1 = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/reprocess",
        json=body, headers=headers,
    )
    assert all(it["status"] == "success" for it in resp1.json()["items"])
    resp2 = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/reprocess",
        json=body, headers=headers,
    )
    assert resp2.json() == resp1.json()
    assert len(ingestion.reprocess_calls) == 2  # one per distinct doc, not doubled


def test_batch_reprocess_max_20(tmp_path):
    client, _1, _2, _3 = _client(tmp_path)
    ids = [str(uuid4()) for _ in range(21)]
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/reprocess",
        json={"document_ids": ids},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_batch_reprocess_within_batch_duplicate(tmp_path):
    client, _1, _2, _ing = _client(tmp_path, with_reprocess=True)
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/reprocess",
        json={"document_ids": [DOC_A, DOC_A]},
    )
    assert resp.status_code == 200, resp.text
    items = _items(resp)
    assert items[0]["status"] == "success"
    assert items[1]["status"] == "error"
    assert items[1]["error"]["code"] == "DUPLICATE_REPROCESS"


def test_batch_reprocess_unknown_doc_error(tmp_path):
    client, _1, _2, _3 = _client(tmp_path, with_reprocess=True)
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/reprocess",
        json={"document_ids": [DOC_A, UNKNOWN_DOC]},
    )
    assert resp.status_code == 200
    items = {it["document_id"]: it for it in _items(resp)}
    assert items[str(DOC_A)]["status"] == "success"
    assert items[str(UNKNOWN_DOC)]["status"] == "error"
    assert items[str(UNKNOWN_DOC)]["error"]["code"] == "DOCUMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# B2.9 — batch delete
# ---------------------------------------------------------------------------

def test_batch_delete_success(tmp_path):
    client, _1, stub, _2 = _client(tmp_path)
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/delete",
        json={"document_ids": [DOC_A, DOC_B]},
    )
    assert resp.status_code == 200, resp.text
    assert all(it["status"] == "success" for it in _items(resp))
    assert sorted(stub.delete_calls) == sorted([(PATH_A, COLLECTION), (PATH_B, COLLECTION)])


def test_batch_delete_no_body_conflict(tmp_path):
    """POST with a JSON body must not collide with route/body handling."""
    client, _1, _2, _3 = _client(tmp_path)
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/delete",
        json={"document_ids": []},
    )
    # deliberately empty → 400 (batch boundary), not a 405/route conflict
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_batch_delete_partial_failure_per_item(tmp_path):
    client, _1, _2, _3 = _client(tmp_path, stub=_StubDoc(delete_errors=True))
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/delete",
        json={"document_ids": [DOC_A, DOC_B]},
    )
    assert resp.status_code == 200, resp.text
    for item in _items(resp):
        assert item["status"] == "error"
        assert item["error"]["code"] == "DELETE_FAILED"


def test_batch_delete_repeated_consistent_errors(tmp_path):
    # First app: doc resolves → deleted. Second app (fresh stub, empty map):
    # the same doc no longer resolves → consistent per-item DOCUMENT_NOT_FOUND.
    client, _1, _2, _3 = _client(tmp_path)
    resp1 = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/delete",
        json={"document_ids": [DOC_A]},
    )
    assert resp1.json()["items"][0]["status"] == "success"

    db2 = WebApiDB(tmp_path / "db2" / "web_api.db")
    services = ApplicationServices(
        query=object(), ingestion=object(), document=_StubDoc({}),
        system=object(), trace=object(), engines=object(), db=db2,
    )
    client2 = TestClient(_build_app(services))
    resp2 = client2.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/delete",
        json={"document_ids": [DOC_A]},
    )
    assert resp2.status_code == 200
    assert resp2.json()["items"][0]["status"] == "error"
    assert resp2.json()["items"][0]["error"]["code"] == "DOCUMENT_NOT_FOUND"
    # and the repeated identical call stays consistent
    resp3 = client2.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/delete",
        json={"document_ids": [DOC_A]},
    )
    assert resp3.json() == resp2.json()


def test_batch_delete_foreign_doc_error(tmp_path):
    stub = _StubDoc({str(DOC_A): (COLLECTION, PATH_A), FOREIGN_DOC: ("elsewhere", "/f.pdf")})
    client, _1, _2, _3 = _client(tmp_path, stub=stub)
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/delete",
        json={"document_ids": [DOC_A, FOREIGN_DOC]},
    )
    assert resp.status_code == 200
    items = {it["document_id"]: it for it in _items(resp)}
    assert items[str(DOC_A)]["status"] == "success"
    assert items[FOREIGN_DOC]["status"] == "error"
    assert items[FOREIGN_DOC]["error"]["code"] == "DOCUMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# Cross-cutting: limits, empty batch
# ---------------------------------------------------------------------------

def test_tags_over_100_rejected(tmp_path):
    client, _1, _2, _3 = _client(tmp_path)
    ids = [str(uuid4()) for _ in range(101)]
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/tags",
        json={"action": "add", "tag_ids": [], "document_ids": ids},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_move_over_100_rejected(tmp_path):
    client, _1, _2, _3 = _client(tmp_path)
    ids = [str(uuid4()) for _ in range(101)]
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/move",
        json={"folder_id": None, "document_ids": ids},
    )
    assert resp.status_code == 400


def test_delete_over_100_rejected(tmp_path):
    client, _1, _2, _3 = _client(tmp_path)
    ids = [str(uuid4()) for _ in range(101)]
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/documents/batch/delete",
        json={"document_ids": ids},
    )
    assert resp.status_code == 400


def test_empty_batch_rejected_400(tmp_path):
    bodies = {
        "tags": {"action": "add", "tag_ids": [], "document_ids": []},
        "move": {"folder_id": None, "document_ids": []},
        "reprocess": {"document_ids": []},
        "delete": {"document_ids": []},
    }
    for path, body in bodies.items():
        client, _1, _2, _3 = _client(tmp_path)
        resp = client.post(
            f"/api/v1/collections/{COLLECTION_ID}/documents/batch/{path}", json=body,
        )
        assert resp.status_code == 400, (path, resp.text)
        assert resp.json()["error"]["code"] == "BAD_REQUEST"