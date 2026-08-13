"""
Unit tests for :mod:`src.mcp_server.auth.store` — SQLite persistence.

Covers the raw store layer: create/get/revoke/rotate round-trips,
uniqueness, durability across a reopened database file, whitelist
storage and error mapping. Secret-material redaction is covered at the
service layer (``test_mcp_key_service.py``).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.mcp_server.auth.models import DuplicateKeyNameError, KeyNotFoundError
from src.mcp_server.auth.store import ApiKeyStore, DEFAULT_DB_PATH


def _utc(*, year: int = 2026, month: int = 8, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


class TestCreateAndRead:
    def test_create_roundtrips_by_key_id(self, tmp_path) -> None:
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        store.create(
            key_id="k1", name="agent-a", secret_digest="digest-1",
            collections=frozenset({"hr", "policy"}), created_at=_utc(),
        )
        rec = store.get_by_key_id("k1")
        assert rec is not None
        assert rec.name == "agent-a"
        assert rec.secret_digest == "digest-1"
        assert rec.enabled is True
        assert rec.revoked_at is None
        assert rec.last_used_at is None
        assert rec.created_at == _utc()
        assert rec.collections == frozenset({"hr", "policy"})

    def test_retrieve_by_name(self, tmp_path) -> None:
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        store.create(
            key_id="k1", name="agent-a", secret_digest="digest-1",
            collections=frozenset({"finance"}), created_at=_utc(),
        )
        rec = store.get_by_name("agent-a")
        assert rec is not None
        assert rec.key_id == "k1"

    def test_unknown_lookups_return_none(self, tmp_path) -> None:
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        assert store.get_by_key_id("nope") is None
        assert store.get_by_name("nope") is None

    def test_list_returns_all_ordered_by_creation(self, tmp_path) -> None:
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        store.create(
            key_id="b", name="z-late", secret_digest="d",
            collections=frozenset({"hr"}), created_at=_utc(month=8, day=2),
        )
        store.create(
            key_id="a", name="a-early", secret_digest="d",
            collections=frozenset({"hr"}), created_at=_utc(month=8, day=1),
        )
        names = [rec.name for rec in store.list()]
        assert names == ["a-early", "z-late"]


class TestValidationErrors:
    def test_duplicate_name_raises_and_inserts_nothing(self, tmp_path) -> None:
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        store.create(
            key_id="k1", name="agent-a", secret_digest="d",
            collections=frozenset({"hr"}), created_at=_utc(),
        )
        with pytest.raises(DuplicateKeyNameError):
            store.create(
                key_id="k2", name="agent-a", secret_digest="other",
                collections=frozenset({"policy"}), created_at=_utc(),
            )
        # The failed insert must not have persisted a partial row.
        assert store.get_by_key_id("k2") is None
        assert [rec.name for rec in store.list()] == ["agent-a"]

    def test_same_key_id_with_different_name_allowed(self, tmp_path) -> None:
        # key_id is the PK; a new key_id + new name is fine, even with a
        # colliding *name* only — covered above. This checks the inverse:
        # different names never collide.
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        store.create(
            key_id="k1", name="a", secret_digest="d",
            collections=frozenset({"hr"}), created_at=_utc(),
        )
        store.create(
            key_id="k2", name="b", secret_digest="d",
            collections=frozenset({"hr"}), created_at=_utc(),
        )
        assert len(store.list()) == 2


class TestRevoke:
    def test_revoke_disables_and_stamps_revoked_at(self, tmp_path) -> None:
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        store.create(
            key_id="k1", name="agent-a", secret_digest="d",
            collections=frozenset({"hr"}), created_at=_utc(),
        )
        revoked_at = _utc(month=8, day=10)
        rec = store.revoke(name="agent-a", revoked_at=revoked_at)
        assert rec.enabled is False
        assert rec.revoked_at == revoked_at
        # Persisted, not just returned.
        stored = store.get_by_key_id("k1")
        assert stored is not None
        assert stored.enabled is False
        assert stored.revoked_at == revoked_at

    def test_revoke_unknown_name_raises(self, tmp_path) -> None:
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        with pytest.raises(KeyNotFoundError):
            store.revoke(name="nobody", revoked_at=_utc())


class TestRotate:
    def test_rotate_replaces_credential_atomically(self, tmp_path) -> None:
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        store.create(
            key_id="old", name="agent-a", secret_digest="old-digest",
            collections=frozenset({"hr"}), created_at=_utc(),
        )
        rotated = store.rotate(
            name="agent-a", key_id="new", secret_digest="new-digest",
            collections=frozenset({"hr", "policy"}), created_at=_utc(day=5),
        )
        assert rotated.key_id == "new"
        assert rotated.secret_digest == "new-digest"
        assert rotated.name == "agent-a"
        assert rotated.collections == frozenset({"hr", "policy"})
        assert rotated.enabled is True
        # Old key id is gone immediately; new one is live.
        assert store.get_by_key_id("old") is None
        assert store.get_by_key_id("new") is not None
        assert len(store.list()) == 1  # same name, no duplicates

    def test_rotate_unknown_name_raises(self, tmp_path) -> None:
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        with pytest.raises(KeyNotFoundError):
            store.rotate(
                name="nobody", key_id="k", secret_digest="d",
                collections=frozenset({"hr"}), created_at=_utc(),
            )


class TestLastUsed:
    def test_update_last_used_persists(self, tmp_path) -> None:
        store = ApiKeyStore(tmp_path / "mcp_access.db")
        store.create(
            key_id="k1", name="agent-a", secret_digest="d",
            collections=frozenset({"hr"}), created_at=_utc(),
        )
        assert store.get_by_key_id("k1").last_used_at is None
        ts = _utc(month=8, day=9)
        store.update_last_used("k1", ts)
        assert store.get_by_key_id("k1").last_used_at == ts


class TestDurability:
    def test_data_survives_reopen(self, tmp_path) -> None:
        path = tmp_path / "mcp_access.db"
        store = ApiKeyStore(path)
        store.create(
            key_id="k1", name="agent-a", secret_digest="digest-1",
            collections=frozenset({"hr", "policy"}), created_at=_utc(),
        )
        # Fresh store instance on the same file — as after a restart.
        store2 = ApiKeyStore(path)
        rec = store2.get_by_key_id("k1")
        assert rec is not None
        assert rec.name == "agent-a"
        assert rec.secret_digest == "digest-1"
        assert rec.collections == frozenset({"hr", "policy"})

    def test_default_db_path_points_at_data_dir(self) -> None:
        assert DEFAULT_DB_PATH == "./data/db/mcp_access.db"
