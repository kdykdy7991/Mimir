"""
B2.1 — tag data model + migration (``WebApiDB``).

Covers: schema creation (migration), collection-scoped uniqueness,
cascade delete (links only, never documents), collection isolation,
Unicode names, and controlled colour tokens.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.application.services.web_store import TAG_COLORS, WebApiDB


@pytest.fixture
def db(tmp_path) -> WebApiDB:
    return WebApiDB(tmp_path / "db" / "web_api.db")


def test_migration_creates_tag_tables(db: WebApiDB) -> None:
    conn = sqlite3.connect(str(db.db_path))
    try:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )
        }
    finally:
        conn.close()
    assert "document_tags" in tables
    assert "document_tag_links" in tables


def test_create_and_get_tag(db: WebApiDB) -> None:
    tag = db.create_tag(collection_id="c1", name=" 财务 ", color="blue")
    assert tag["name"] == "财务"
    assert tag["normalized_name"] == "财务"
    assert tag["color"] == "blue"
    fetched = db.get_tag(tag["tag_id"])
    assert fetched is not None and fetched["name"] == "财务"


def test_unique_normalized_name_per_collection(db: WebApiDB) -> None:
    db.create_tag(collection_id="c1", name="Dataset")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_tag(collection_id="c1", name="dataset")


def test_same_name_allowed_across_collections(db: WebApiDB) -> None:
    a = db.create_tag(collection_id="c1", name="导入")
    b = db.create_tag(collection_id="c2", name="导入")
    assert a["tag_id"] != b["tag_id"]


def test_name_length_validation(db: WebApiDB) -> None:
    with pytest.raises(ValueError):
        db.create_tag(collection_id="c1", name="  ")  # empty after trim
    with pytest.raises(ValueError):
        db.create_tag(collection_id="c1", name="x" * 65)  # too long


def test_color_token_validation(db: WebApiDB) -> None:
    db.create_tag(collection_id="c1", name="ok", color="green")
    assert "green" in TAG_COLORS
    with pytest.raises(ValueError):
        db.create_tag(collection_id="c1", name="bad-color", color="#ff0000")


def test_unicode_name(db: WebApiDB) -> None:
    tag = db.create_tag(collection_id="c1", name=" 中文标签 ｔｅｓｔ")
    assert tag["normalized_name"] == " 中文标签 ｔｅｓｔ".strip().casefold()


def test_list_tags_ordered_and_scoped(db: WebApiDB) -> None:
    db.create_tag(collection_id="c1", name="zebra")
    db.create_tag(collection_id="c1", name="Alpha")
    db.create_tag(collection_id="c2", name="other")
    names = [t["name"] for t in db.list_tags("c1")]
    assert names == ["Alpha", "zebra"]  # normalized_name ASC


def test_set_document_tags_replaces_and_cascades(db: WebApiDB) -> None:
    t1 = db.create_tag(collection_id="c1", name="a")
    t2 = db.create_tag(collection_id="c1", name="b")
    db.set_document_tags("doc1", [t1["tag_id"], t2["tag_id"]])
    assert set(db.document_tag_ids("doc1")) == {t1["tag_id"], t2["tag_id"]}
    # replace -> drop t1, keep only t2
    db.set_document_tags("doc1", [t2["tag_id"]])
    assert db.document_tag_ids("doc1") == [t2["tag_id"]]

    # deleting the tag removes links but leaves the doc untouched
    db.delete_tag(t2["tag_id"])
    assert db.document_tag_ids("doc1") == []


def test_delete_tag_returns_false_for_unknown(db: WebApiDB) -> None:
    assert db.delete_tag("nope") is False


def test_count_tag_links(db: WebApiDB) -> None:
    t1 = db.create_tag(collection_id="c1", name="x")
    db.set_document_tags("doc1", [t1["tag_id"]])
    db.set_document_tags("doc2", [t1["tag_id"]])
    assert db.count_tag_links(t1["tag_id"]) == 2


def test_update_tag_recomputes_normalized_name(db: WebApiDB) -> None:
    t = db.create_tag(collection_id="c1", name="Old")
    db.update_tag(tag_id=t["tag_id"], name="New", color="red")
    updated = db.get_tag(t["tag_id"])
    assert updated["name"] == "New"
    assert updated["normalized_name"] == "new"
    assert updated["color"] == "red"