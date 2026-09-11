"""
B2.4 — folder CRUD + document move endpoints.

Coverage: create/rename/move/delete, document placement, sibling conflicts,
cycle/move errors, collection scope (anti-enumeration), unknown document.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.application.services.web_store import WebApiDB
from src.web_api.app import create_app
from src.web_api.mappers import collection_uuid

COLLECTION = "kb"
COLLECTION_ID = collection_uuid(COLLECTION)
OTHER_COLLECTION_ID = collection_uuid("elsewhere")
DOC_ID = "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f"


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


def test_folder_crud_flow(tmp_path) -> None:
    client, db, _ = _client(tmp_path)
    created = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "研发"},
    )
    assert created.status_code == 201, created.text
    fid = created.json()["id"]
    assert created.json()["depth"] == 0

    child = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders",
        json={"name": "前端", "parent_id": fid},
    )
    assert child.status_code == 201
    assert child.json()["depth"] == 1

    listed = client.get(f"/api/v1/collections/{COLLECTION_ID}/folders").json()
    assert [f["name"] for f in listed["items"]] == ["研发", "前端"]

    renamed = client.patch(
        f"/api/v1/collections/{COLLECTION_ID}/folders/{fid}", json={"name": "Design"},
    )
    assert renamed.json()["name"] == "Design"

    # delete the child (empty) then the root (now empty since no docs placed)
    deleted = client.delete(f"/api/v1/collections/{COLLECTION_ID}/folders/{child.json()['id']}")
    assert deleted.status_code == 204
    assert db.get_folder(child.json()["id"]) is None
    deleted_root = client.delete(f"/api/v1/collections/{COLLECTION_ID}/folders/{fid}")
    assert deleted_root.status_code == 204
    assert db.get_folder(fid) is None


def test_delete_non_empty_folder_409(tmp_path) -> None:
    client, db, _ = _client(tmp_path)
    parent = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "parent"},
    ).json()
    client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders",
        json={"name": "child", "parent_id": parent["id"]},
    )
    resp = client.delete(f"/api/v1/collections/{COLLECTION_ID}/folders/{parent['id']}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"
    # still present after rejected delete
    assert db.get_folder(parent["id"]) is not None


def test_sibling_name_conflict_409(tmp_path) -> None:
    client, _, _ = _client(tmp_path)
    root = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "A"},
    ).json()
    client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "B", "parent_id": root["id"]},
    )
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "b", "parent_id": root["id"]},
    )
    assert resp.status_code == 409


def test_unknown_parent_400(tmp_path) -> None:
    client, _, _ = _client(tmp_path)
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "x", "parent_id": "nope"},
    )
    assert resp.status_code == 400


def test_cycle_move_rejected_400(tmp_path) -> None:
    client, _, _ = _client(tmp_path)
    a = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "a"},
    ).json()
    b = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "b", "parent_id": a["id"]},
    ).json()
    c = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "c", "parent_id": b["id"]},
    ).json()
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders/{a['id']}/move", json={"parent_id": c["id"]},
    )
    assert resp.status_code == 400


def test_foreign_folder_not_found_404(tmp_path) -> None:
    client, db, _ = _client(tmp_path)
    foreign = db.create_folder(collection_id=str(OTHER_COLLECTION_ID), name="away")["folder_id"]
    resp = client.patch(
        f"/api/v1/collections/{COLLECTION_ID}/folders/{foreign}", json={"name": "zzz"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "FOLDER_NOT_FOUND"


def test_unknown_collection_404(tmp_path) -> None:
    client, _, _ = _client(tmp_path)
    resp = client.get(f"/api/v1/collections/{OTHER_COLLECTION_ID}/folders")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "COLLECTION_NOT_FOUND"


def test_document_folder_set_and_clear(tmp_path) -> None:
    client, db, _ = _client(tmp_path)
    fid = db.create_folder(collection_id=str(COLLECTION_ID), name="work")["folder_id"]
    set_resp = client.put(
        f"/api/v1/documents/{DOC_ID}/folder", json={"folder_id": fid},
    )
    assert set_resp.status_code == 200, set_resp.text
    assert set_resp.json()["folder_id"] == fid
    assert db.document_placement(DOC_ID)["folder_id"] == fid

    clear = client.put(f"/api/v1/documents/{DOC_ID}/folder", json={"folder_id": None})
    assert clear.json()["folder_id"] is None
    assert db.document_placement(DOC_ID)["folder_id"] is None


def test_document_folder_cross_collection_400(tmp_path) -> None:
    client, db, _ = _client(tmp_path)
    foreign = db.create_folder(collection_id=str(OTHER_COLLECTION_ID), name="else")["folder_id"]
    resp = client.put(
        f"/api/v1/documents/{DOC_ID}/folder", json={"folder_id": foreign},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_document_folder_unknown_document_404(tmp_path) -> None:
    db = WebApiDB(tmp_path / "db" / "web_api.db")
    stub = _StubDoc()
    stub.resolve = False
    client = TestClient(create_app(ApplicationServices(
        query=object(), ingestion=object(), document=stub,
        system=object(), trace=object(), engines=object(), db=db,
    )))
    resp = client.put(f"/api/v1/documents/{DOC_ID}/folder", json={"folder_id": None})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


class _TwoCollDoc:
    resolve = True

    def resolve_document_id(self, document_id):
        return (COLLECTION, f"/data/uploads/{COLLECTION}/x.pdf")

    def list_collections(self):
        ref = type("R", (), {})
        ref.name = COLLECTION
        ref2 = type("R", (), {})
        ref2.name = "elsewhere"
        return [ref, ref2]


def test_cross_collection_child_creation_rejected(tmp_path) -> None:
    """P0: a child folder cannot hang off a parent in another collection (HTTP)."""
    db = WebApiDB(tmp_path / "db" / "web_api.db")
    services = ApplicationServices(
        query=object(), ingestion=object(), document=_TwoCollDoc(),
        system=object(), trace=object(), engines=object(), db=db,
    )
    client = TestClient(create_app(services=services))
    parent = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "pa"},
    ).json()
    other = collection_uuid("elsewhere")
    resp = client.post(
        f"/api/v1/collections/{other}/folders",
        json={"name": "child", "parent_id": parent["id"]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_move_onto_same_name_sibling_is_409(tmp_path) -> None:
    """P1: moving a folder under a parent that already has a same-name
    sibling must be a 409 conflict, not a 500."""
    db = WebApiDB(tmp_path / "db" / "web_api.db")
    services = ApplicationServices(
        query=object(), ingestion=object(), document=_StubDoc(),
        system=object(), trace=object(), engines=object(), db=db,
    )
    client = TestClient(create_app(services=services))
    x = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "X"},
    ).json()
    y = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders", json={"name": "Y"},
    ).json()
    nx = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders",
        json={"name": "n", "parent_id": x["id"]},
    ).json()
    client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders",
        json={"name": "n", "parent_id": y["id"]},
    )
    # move X/n into Y (already has a sibling named 'n') → 409
    resp = client.post(
        f"/api/v1/collections/{COLLECTION_ID}/folders/{nx['id']}/move",
        json={"parent_id": y["id"]},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"