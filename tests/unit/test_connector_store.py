from __future__ import annotations

import base64

import pytest

from src.connectors.store import (
    CheckpointConflictError, CredentialCipher, CredentialKeyMissingError,
    DataSourceStore,
)


def test_missing_master_key_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv(CredentialCipher.ENV_NAME, raising=False)
    with pytest.raises(CredentialKeyMissingError):
        CredentialCipher.from_env()


def test_credentials_are_encrypted_and_never_returned_in_metadata(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = store.create(
        source_id="source-1", name="release feed", connector_type="rss",
        collection_id="manuals", policy={"deletion": "tombstone"},
        credentials={"username": "alice", "password": "super-secret"},
    )
    assert not hasattr(source, "credentials")
    assert store.credentials("source-1") == {
        "username": "alice", "password": "super-secret",
    }
    raw = (tmp_path / "sources.db").read_bytes()
    assert b"super-secret" not in raw and b"alice" not in raw


def test_checkpoint_compare_and_swap_prevents_lost_updates(tmp_path) -> None:
    store = DataSourceStore(tmp_path / "sources.db", CredentialCipher(b"k" * 32))
    source = store.create(
        source_id="source-1", name="feed", connector_type="rss",
        collection_id="manuals", policy={}, credentials={},
    )
    updated = store.update_checkpoint(
        source.id, {"etag": "v1", "cursor": "10"}, expected_revision=0,
    )
    assert updated.checkpoint_revision == 1
    with pytest.raises(CheckpointConflictError):
        store.update_checkpoint(source.id, {"etag": "stale"}, expected_revision=0)
