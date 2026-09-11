"""
B2.2 — tag CRUD + document tag binding endpoints.

Coverage: CRUD, full-replace binding, cross-collection tag rejection,
duplicate id dedup, unknown document, collection-not-found, anti-enumeration.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.application.services.web_store import WebApiDB
from src.web_api.app import create_app
from src.web_api.mappers import collection_uuid

COLLECTION = "kb"
COLLECTION_ID = collection_uuid(COLLECTION)
OTHER_COLLECTION_ID = collection_uuid("elsewhere")
DOC_ID = "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f"
DOC_OTHER = "aaaaaaaa-0000-0000-0000-000000000001"


class _StubDoc:
    resolve = True

    def resolve_document_id(self, document_id):
        if self.resolve:
            return (COLLECTION, f"/data/uploads/{COLLECTION}/x.pdf")
        return None

    def list_collections(self):
        ref = type("R", (), {})
        ref.name = COLLECTION
        return [ref]


def _client(tmp_path) -> tuple[TestClient, WebApiDB, _StubDoc]:
    stub = _StubDoc()
    db = WebApiDB(tmp_path / "db" / "web_api.db")
    services = ApplicationServices(
        query=object(), ingestion=object(), document=stub,
        system=object(), trace=object(), engines=object(), db=db,
    )
    return TestClient(create_app(services=services)), db, stub


def test_tag_crud_flow(tmp_path) -> None:
    client, db, _ = _client(tmp_path)
    # create
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/tags",
        json={"name": "财务", "color": "blue"},
    )
    assert resp.status_code == 201, resp.text
    tag = resp.json()
    assert tag["name"] == "财务"
    assert tag["color"] == "blue"
    tid = tag["id"]

    # list
    listed = client.get(f"/api/v1/collections/{COLLECTION_ID}/tags").json()
    assert [t["name"] for t in listed["items"]] == ["财务"]

    # patch
    patched = client.patch(
        f"/api/v1/collections/{COLLECTION_ID}/tags/{tid}",
        json={"name": "Finance", "color": "green"},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Finance"
    assert patched.json()["color"] == "green"

    # delete
    deleted = client.delete(f"/api/v1/collections/{COLLECTION_ID}/tags/{tid}")
    assert deleted.status_code == 204
    assert db.get_tag(tid) is None


def test_create_duplicate_name_is_conflict(tmp_path) -> None:
    client, _, _ = _client(tmp_path)
    client.post(f"/api/v1/collections/{COLLECTION_ID}/tags", json={"name": "Sales"})
    resp = client.post(f"/api/v1/collections/{COLLECTION_ID}/tags", json={"name": "sales"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


def test_create_invalid_color_is_bad_request(tmp_path) -> None:
    client, _, _ = _client(tmp_path)
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/tags",
        json={"name": "x", "color": "#ff0000"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_create_in_nonexistent_collection_is_404(tmp_path) -> None:
    client, _, _ = _client(tmp_path)
    resp = client.post(f"/api/v1/collections/{DOC_OTHER}/tags", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "COLLECTION_NOT_FOUND"


def test_patch_delete_foreign_tag_is_not_found(tmp_path) -> None:
    client, db, _ = _client(tmp_path)
    # tag in another collection
    foreign = db.create_tag(collection_id=str(OTHER_COLLECTION_ID), name="away")
    c = client
    p = c.patch(
        f"/api/v1/collections/{COLLECTION_ID}/tags/{foreign['tag_id']}",
        json={"name": "zzz"},
    )
    assert p.status_code == 404
    assert p.json()["error"]["code"] == "TAG_NOT_FOUND"
    d = c.delete(f"/api/v1/collections/{COLLECTION_ID}/tags/{foreign['tag_id']}")
    assert d.status_code == 404


def test_document_tags_full_replace(tmp_path) -> None:
    client, db, _ = _client(tmp_path)
    t1 = db.create_tag(collection_id=str(COLLECTION_ID), name="a")["tag_id"]
    t2 = db.create_tag(collection_id=str(COLLECTION_ID), name="b")["tag_id"]
    t3 = db.create_tag(collection_id=str(COLLECTION_ID), name="c")["tag_id"]

    body = {"tag_ids": [t1, t2]}
    resp = client.put(f"/api/v1/documents/{DOC_ID}/tags", json=body)
    assert resp.status_code == 200, resp.text
    assert set(resp.json()["tag_ids"]) == {t1, t2}

    # replace with [t2, t3] -> t1 dropped
    client.put(f"/api/v1/documents/{DOC_ID}/tags", json={"tag_ids": [t2, t3]})
    assert set(db.document_tag_ids(DOC_ID)) == {t2, t3}

    # empty clears all
    client.put(f"/api/v1/documents/{DOC_ID}/tags", json={"tag_ids": []})
    assert db.document_tag_ids(DOC_ID) == []


def test_document_tags_duplicate_ids_dedup(tmp_path) -> None:
    client, db, _ = _client(tmp_path)
    t1 = db.create_tag(collection_id=str(COLLECTION_ID), name="a")["tag_id"]
    resp = client.put(
        f"/api/v1/documents/{DOC_ID}/tags", json={"tag_ids": [t1, t1, t1]},
    )
    assert resp.status_code == 200
    assert set(resp.json()["tag_ids"]) == {t1}
    assert db.document_tag_ids(DOC_ID) == [t1]


def test_document_tags_cross_collection_rejected(tmp_path) -> None:
    client, db, _ = _client(tmp_path)
    own = db.create_tag(collection_id=str(COLLECTION_ID), name="mine")["tag_id"]
    foreign = db.create_tag(collection_id=str(OTHER_COLLECTION_ID), name="other")["tag_id"]
    resp = client.put(
        f"/api/v1/documents/{DOC_ID}/tags", json={"tag_ids": [own, foreign]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"
    # no partial write happened
    assert db.document_tag_ids(DOC_ID) == []


def test_document_tags_unknown_document_404(tmp_path) -> None:
    db = WebApiDB(tmp_path / "db" / "web_api.db")
    stub = _StubDoc()
    stub.resolve = False
    client = TestClient(create_app(ApplicationServices(
        query=object(), ingestion=object(), document=stub,
        system=object(), trace=object(), engines=object(), db=db,
    )))
    resp = client.put(
        f"/api/v1/documents/{DOC_ID}/tags", json={"tag_ids": []},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"