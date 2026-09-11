"""
B2.3 — folder data model + migration (``WebApiDB``).

Covers: schema, sibling-name uniqueness, depth cap, cycle protection on
move, re-parent on delete, and document placement.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.application.services.web_store import MAX_FOLDER_DEPTH, WebApiDB


@pytest.fixture
def db(tmp_path) -> WebApiDB:
    return WebApiDB(tmp_path / "db" / "web_api.db")


def test_migration_creates_folder_tables(db: WebApiDB) -> None:
    conn = sqlite3.connect(str(db.db_path))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "document_folders" in tables
    assert "document_placements" in tables


def test_create_folder_root_and_nested(db: WebApiDB) -> None:
    root = db.create_folder(collection_id="c1", name="研发")
    assert root["depth"] == 0
    assert root["parent_id"] is None
    child = db.create_folder(collection_id="c1", name="前端", parent_id=root["folder_id"])
    assert child["depth"] == 1
    assert child["parent_id"] == root["folder_id"]


def test_sibling_name_unique_per_parent(db: WebApiDB) -> None:
    root = db.create_folder(collection_id="c1", name="A")
    db.create_folder(collection_id="c1", name="B", parent_id=root["folder_id"])
    # same normalized name under the same parent -> IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        db.create_folder(collection_id="c1", name="b", parent_id=root["folder_id"])
    # same name under a different parent is fine
    db.create_folder(collection_id="c1", name="b")


def test_unknown_parent_rejected(db: WebApiDB) -> None:
    with pytest.raises(ValueError):
        db.create_folder(collection_id="c1", name="x", parent_id="nope")


def test_depth_cap(db: WebApiDB) -> None:
    parent = None
    for depth in range(MAX_FOLDER_DEPTH + 1):  # depths 0..5 reachable
        folder = db.create_folder(collection_id="c1", name=f"l{depth}", parent_id=parent)
        assert folder["depth"] == depth
        parent = folder["folder_id"]
    # one more beyond the cap -> error
    with pytest.raises(ValueError):
        db.create_folder(collection_id="c1", name="too-deep", parent_id=parent)


def test_move_folder_cycle_prevented(db: WebApiDB) -> None:
    a = db.create_folder(collection_id="c1", name="a")
    b = db.create_folder(collection_id="c1", name="b", parent_id=a["folder_id"])
    c = db.create_folder(collection_id="c1", name="c", parent_id=b["folder_id"])
    with pytest.raises(ValueError):
        db.move_folder(folder_id=a["folder_id"], new_parent_id=c["folder_id"])


def test_move_folder_recomputes_depth_and_children(db: WebApiDB) -> None:
    root = db.create_folder(collection_id="c1", name="root")
    a = db.create_folder(collection_id="c1", name="a", parent_id=root["folder_id"])
    b = db.create_folder(collection_id="c1", name="b", parent_id=a["folder_id"])
    # move b to root
    db.move_folder(folder_id=b["folder_id"], new_parent_id=None)
    assert db.get_folder(b["folder_id"])["depth"] == 0
    assert db.get_folder(b["folder_id"])["parent_id"] is None


def test_delete_folder_reparents_children_and_documents(db: WebApiDB) -> None:
    root = db.create_folder(collection_id="c1", name="root")
    parent = db.create_folder(collection_id="c1", name="parent", parent_id=root["folder_id"])
    child = db.create_folder(collection_id="c1", name="child", parent_id=parent["folder_id"])
    db.move_document(document_id="doc1", folder_id=parent["folder_id"], collection_id="c1")
    db.move_document(document_id="doc2", folder_id=parent["folder_id"], collection_id="c1")
    db.move_document(document_id="docX", folder_id=child["folder_id"], collection_id="c1")

    result = db.delete_folder(parent["folder_id"])
    assert result["reparented_folders"] == 1
    assert result["reparented_documents"] == 2
    # child was moved up to root; doc1/doc2 moved to root
    assert db.get_folder(child["folder_id"])["parent_id"] == root["folder_id"]
    assert db.document_placement("doc1")["folder_id"] == root["folder_id"]
    # docX stays in child, which is now under root
    assert db.document_placement("docX")["folder_id"] == child["folder_id"]


def test_documents_by_folder_and_counts(db: WebApiDB) -> None:
    root = db.create_folder(collection_id="c1", name="root")
    db.move_document(document_id="a", folder_id=root["folder_id"], collection_id="c1")
    db.move_document(document_id="b", folder_id=root["folder_id"], collection_id="c1")
    db.move_document(document_id="c", folder_id=None, collection_id="c1")
    assert set(db.documents_by_folder(root["folder_id"], "c1")) == {"a", "b"}
    assert set(db.documents_by_folder(None, "c1")) == {"c"}
    assert db.count_documents_in_folder(root["folder_id"]) == 2


def test_move_document_upsert_and_clear(db: WebApiDB) -> None:
    f = db.create_folder(collection_id="c1", name="f")
    db.move_document(document_id="d", folder_id=f["folder_id"], collection_id="c1")
    assert db.document_placement("d")["folder_id"] == f["folder_id"]
    # clearing (None) moves to root
    db.move_document(document_id="d", folder_id=None, collection_id="c1")
    assert db.document_placement("d")["folder_id"] is None


def test_list_folders_stable_order(db: WebApiDB) -> None:
    db.create_folder(collection_id="c1", name="zebra")
    db.create_folder(collection_id="c1", name="Alpha")
    db.create_folder(collection_id="c2", name="other")
    names = [f["name"] for f in db.list_folders("c1")]
    assert names == ["Alpha", "zebra"]


def test_folder_name_validation(db: WebApiDB) -> None:
    with pytest.raises(ValueError):
        db.create_folder(collection_id="c1", name="  ")
    with pytest.raises(ValueError):
        db.create_folder(collection_id="c1", name="x" * 65)