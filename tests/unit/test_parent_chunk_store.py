from __future__ import annotations

import pytest

from src.application.contracts import content_version
from src.core.types import Chunk
from src.ingestion.chunking import ParentChunkBuilder
from src.ingestion.storage import ParentChunkStore, ParentChunkStoreError


def _version(text):
    children = [Chunk(
        id=f"child-{text}", text=text,
        metadata={"document_version": content_version("doc", text)},
        start_offset=0, end_offset=len(text), source_ref="doc",
    )]
    built = ParentChunkBuilder().build(children)
    return built.children, built.parents, children[0].metadata["document_version"]


def test_stage_and_atomic_active_version_switch(tmp_path):
    store = ParentChunkStore(tmp_path / "parents.db")
    children1, parents1, version1 = _version("one")
    store.stage(collection="kb", document_id="doc", version=version1,
                children=children1, parents=parents1)
    assert store.active_version("kb", "doc") is None
    store.activate(collection="kb", document_id="doc", version=version1)
    assert store.parent_for_child("kb", "doc", "child-one").text == "one"

    children2, parents2, version2 = _version("two")
    store.stage(collection="kb", document_id="doc", version=version2,
                children=children2, parents=parents2)
    assert store.active_version("kb", "doc") == version1
    store.activate(collection="kb", document_id="doc", version=version2)
    assert store.active_version("kb", "doc") == version2
    assert store.parent_for_child("kb", "doc", "child-two").text == "two"


def test_staging_an_already_active_version_is_idempotent(tmp_path):
    store = ParentChunkStore(tmp_path / "parents.db")
    children, parents, version = _version("same")
    assert store.stage(
        collection="kb", document_id="doc", version=version,
        children=children, parents=parents,
    ) is True
    store.activate(collection="kb", document_id="doc", version=version)

    assert store.stage(
        collection="kb", document_id="doc", version=version,
        children=children, parents=parents,
    ) is False
    assert store.active_version("kb", "doc") == version


def test_invalid_stage_and_failed_activation_preserve_active(tmp_path):
    store = ParentChunkStore(tmp_path / "parents.db")
    children, parents, version = _version("good")
    store.stage(collection="kb", document_id="doc", version=version,
                children=children, parents=parents)
    store.activate(collection="kb", document_id="doc", version=version)

    bad = Chunk(id="bad", text="bad", metadata={"parent_chunk_id": "missing"},
                source_ref="doc")
    with pytest.raises(ParentChunkStoreError):
        store.stage(collection="kb", document_id="doc", version="bad",
                    children=[bad], parents=[])
    with pytest.raises(ParentChunkStoreError):
        store.activate(collection="kb", document_id="doc", version="bad")
    assert store.active_version("kb", "doc") == version


def test_delete_staging_is_idempotent_and_cannot_delete_active(tmp_path):
    store = ParentChunkStore(tmp_path / "parents.db")
    children, parents, version = _version("x")
    store.stage(collection="kb", document_id="doc", version=version,
                children=children, parents=parents)
    store.delete_staging("kb", "doc", version)
    store.delete_staging("kb", "doc", version)
    assert store.active_version("kb", "doc") is None


def test_delete_document_is_idempotent(tmp_path):
    store = ParentChunkStore(tmp_path / "parents.db")
    children, parents, version = _version("x")
    store.stage(collection="kb", document_id="doc", version=version,
                children=children, parents=parents)
    store.activate(collection="kb", document_id="doc", version=version)
    store.delete_document("kb", "doc")
    store.delete_document("kb", "doc")
    assert store.active_version("kb", "doc") is None


def test_restore_active_supports_compensation_and_no_previous_version(tmp_path):
    store = ParentChunkStore(tmp_path / "parents.sqlite3")
    children1, parents1, version1 = _version("one")
    store.stage(collection="kb", document_id="doc", version=version1,
                children=children1, parents=parents1)
    store.activate(collection="kb", document_id="doc", version=version1)
    children2, parents2, version2 = _version("two")
    store.stage(collection="kb", document_id="doc", version=version2,
                children=children2, parents=parents2)
    store.activate(collection="kb", document_id="doc", version=version2)
    store.restore_active("kb", "doc", version1)
    assert store.active_version("kb", "doc") == version1
    store.restore_active("kb", "doc", None)
    assert store.active_version("kb", "doc") is None
