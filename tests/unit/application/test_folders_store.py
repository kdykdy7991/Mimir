"""
B2.3 — folder data model + migration (``WebApiDB``).

Covers: schema, sibling-name uniqueness, depth cap, cycle protection on
move, re-parent on delete, and document placement.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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


def test_delete_empty_folder(db: WebApiDB) -> None:
    root = db.create_folder(collection_id="c1", name="root")
    result = db.delete_folder(root["folder_id"])
    assert result == {"reparented_folders": 0, "reparented_documents": 0}
    assert db.get_folder(root["folder_id"]) is None


def test_delete_non_empty_folder_rejected(db: WebApiDB) -> None:
    from src.application.services.web_store import FolderNotEmptyError

    parent = db.create_folder(collection_id="c1", name="parent")
    # child present -> non-empty
    db.create_folder(collection_id="c1", name="child", parent_id=parent["folder_id"])
    with pytest.raises(FolderNotEmptyError):
        db.delete_folder(parent["folder_id"])
    # document present -> non-empty
    other = db.create_folder(collection_id="c1", name="other")
    db.move_document(document_id="doc1", folder_id=other["folder_id"], collection_id="c1")
    with pytest.raises(FolderNotEmptyError):
        db.delete_folder(other["folder_id"])


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


def test_child_folder_must_be_same_collection(db: WebApiDB) -> None:
    """P0: a child folder's parent must belong to the SAME collection."""
    parent = db.create_folder(collection_id="c1", name="a")
    with pytest.raises(ValueError):
        db.create_folder(collection_id="c2", name="b", parent_id=parent["folder_id"])


def test_root_sibling_duplicate_normalized_name_rejected(db: WebApiDB) -> None:
    """P1: same normalized name under the collection root must be rejected.

    The DB unique index cannot catch NULL-parent duplicates, so the store
    guard must reject them (root included).
    """
    db.create_folder(collection_id="c1", name="研究")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_folder(collection_id="c1", name=" 研究 ")
    # a different collection is fine
    db.create_folder(collection_id="c2", name="研究")


def test_concurrent_root_sibling_creation_has_one_winner(db: WebApiDB) -> None:
    """BEGIN IMMEDIATE makes the NULL-root uniqueness guard race-safe."""
    def create():
        try:
            db.create_folder(collection_id="c1", name="Concurrent")
            return "created"
        except sqlite3.IntegrityError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: create(), range(2)))
    assert sorted(outcomes) == ["conflict", "created"]
    assert [row["name"] for row in db.list_folders("c1")] == ["Concurrent"]


def test_move_subtree_that_exceeds_max_depth_rejected(db: WebApiDB) -> None:
    """P1: moving a deep subtree can exceed the max depth even when the
    moved root itself would fit; reject before writing."""
    # Build a valid depth-0 subtree with descendants down to depth MAX (0..5).
    subtree_root = db.create_folder(collection_id="c1", name="sub")
    prev = subtree_root
    for d in range(1, MAX_FOLDER_DEPTH + 1):  # depths 1..5 relative to root
        prev = db.create_folder(collection_id="c1", name=f"l{d}", parent_id=prev["folder_id"])
    # Moving the subtree (relative depth = MAX) below another root-level folder
    # would push its deepest descendant to depth MAX+1 > MAX.
    other = db.create_folder(collection_id="c1", name="other")
    with pytest.raises(ValueError):
        db.move_folder(folder_id=subtree_root["folder_id"], new_parent_id=other["folder_id"])
    # moving it to the root (depth 0) is still allowed (1+5 <= 5 is not the case
    # at root, but depth 0 + rel 5 is 5 and fits) — assert not blocked when it fits.
    db.move_folder(folder_id=subtree_root["folder_id"], new_parent_id=None)
