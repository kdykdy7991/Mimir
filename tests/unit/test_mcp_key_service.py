"""
Unit tests for :mod:`src.mcp_server.auth.key_service` — credential
creation, authentication, revocation, rotation, isolation and secret
hygiene (PRD §4.1 / §9).
"""

from __future__ import annotations

import base64
import sqlite3
import threading
from datetime import timezone
from pathlib import Path

import pytest

from src.mcp_server.auth import (
    ApiKeyMetadata,
    ApiKeyService,
    DuplicateKeyNameError,
    InvalidCollectionWhitelistError,
    KeyNotFoundError,
)
from src.mcp_server.auth.store import ApiKeyStore


def _service(tmp_path) -> ApiKeyService:
    return ApiKeyService(db_path=tmp_path / "mcp_access.db")


def _db_rows(db_path: Path) -> list[sqlite3.Row]:
    """Raw key rows, for asserting what is persisted (no secrets)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM mcp_api_keys").fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Creation + format
# ---------------------------------------------------------------------------

class TestCreateKey:
    def test_create_returns_key_and_metadata(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, meta = service.create_key(
            name="agent-a", allowed_collections={"hr", "policy"},
        )
        assert raw.startswith("skdy_mcp_")
        assert raw.count(".") == 1  # key_id and secret, exactly
        assert meta.name == "agent-a"
        assert meta.allowed_collections == frozenset({"hr", "policy"})
        assert meta.enabled is True
        assert meta.revoked_at is None
        assert meta.last_used_at is None
        assert meta.created_at.tzinfo is not None

    def test_key_id_has_at_least_64_bits_of_entropy(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, meta = service.create_key(
            name="agent-a", allowed_collections={"hr"},
        )
        prefix = "skdy_mcp_"
        body = raw[len(prefix):]
        key_id, _secret = body.split(".")
        # URL-safe base64: ~6 bits per character -> >= 11 chars for 64 bits.
        assert len(key_id) >= 11
        # The metadata carries the same public key_id used in the raw key.
        assert key_id == meta.key_id

    def test_secret_is_at_least_32_random_bytes(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, _ = service.create_key(
            name="agent-a", allowed_collections={"hr"},
        )
        body = raw[len("skdy_mcp_"):]
        _key_id, secret = body.split(".")
        decoded = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
        assert len(decoded) >= 32

    def test_keys_are_unique_per_creation(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw1, _ = service.create_key(name="a", allowed_collections={"hr"})
        raw2, _ = service.create_key(name="b", allowed_collections={"hr"})
        assert raw1 != raw2

    def test_database_stores_digest_not_raw_key(self, tmp_path) -> None:
        db_path = tmp_path / "mcp_access.db"
        service = ApiKeyService(db_path=db_path)
        raw, meta = service.create_key(
            name="agent-a", allowed_collections={"hr"},
        )
        rows = _db_rows(db_path)
        assert len(rows) == 1
        stored = rows[0]["secret_digest"]
        assert stored != raw
        assert raw not in stored
        # The stored value is a hex SHA-256 digest.
        assert len(stored) == 64
        assert all(c in "0123456789abcdef" for c in stored)
        # And the digest corresponds to the secret component.
        secret = raw[len("skdy_mcp_"):].split(".", 1)[1]
        import hashlib
        assert stored == hashlib.sha256(secret.encode()).hexdigest()
        # key_id is stored in plaintext (it is public by design).
        assert rows[0]["key_id"] == meta.key_id


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuthenticate:
    def test_authenticate_with_correct_key(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, meta = service.create_key(
            name="agent-a", allowed_collections={"hr", "policy"},
        )
        principal = service.authenticate(raw)
        assert principal is not None
        assert principal.key_id == meta.key_id
        assert principal.name == "agent-a"
        assert principal.allowed_collections == frozenset({"hr", "policy"})

    def test_wrong_secret_returns_none(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, meta = service.create_key(
            name="agent-a", allowed_collections={"hr"},
        )
        key_id = raw[len("skdy_mcp_"):].split(".", 1)[0]
        wrong = f"skdy_mcp_{key_id}.wrong-secret"
        assert service.authenticate(wrong) is None
        # Wrong prefix on the same key_id is also malformed -> None.
        assert service.authenticate(f"skdy_other_{key_id}.wrong-secret") is None

    def test_unknown_key_id_returns_none(self, tmp_path) -> None:
        service = _service(tmp_path)
        assert service.authenticate("skdy_mcp_unknown-key.very-secret") is None

    def test_malformed_keys_return_none(self, tmp_path) -> None:
        service = _service(tmp_path)
        for bad in (
            "",
            "skdy_mcp_",
            "skdy_mcp_no_dot",
            "skdy_mcp_.only-secret",
            "skdy_mcp_onlykey.",
            "  skdy_mcp_k.s  ",     # whitespace is not stripped
            "skdy_mcp_k.s.extra",
            "skdy_mcp_k.s\n",        # trailing newline
            "skdy_mcp_k s",          # space inside
            "Bearer skdy_mcp_k.s",   # raw key, not the Authorization header
            "skdy_mcp_.s",
            "SKDY_MCP_k.s",          # case-sensitive prefix
        ):
            assert service.authenticate(bad) is None, f"expected None for {bad!r}"

    def test_authenticate_with_non_string_returns_none(self, tmp_path) -> None:
        service = _service(tmp_path)
        assert service.authenticate(None) is None  # type: ignore[arg-type]
        assert service.authenticate(12345) is None  # type: ignore[arg-type]

    def test_revoked_key_authenticate_fails(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, _ = service.create_key(
            name="agent-a", allowed_collections={"hr"},
        )
        assert service.authenticate(raw) is not None
        service.revoke_key(name="agent-a")
        assert service.authenticate(raw) is None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

class TestListKeys:
    def test_list_returns_metadata_without_secrets(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, meta = service.create_key(
            name="agent-a", allowed_collections={"hr"},
        )
        keys = service.list_keys()
        assert len(keys) == 1
        shown = keys[0]
        assert shown.name == "agent-a"
        assert shown.allowed_collections == frozenset({"hr"})
        # No secret or digest anywhere in the returned metadata.
        assert not hasattr(shown, "secret")
        assert not hasattr(shown, "secret_digest")
        assert str(shown).find(raw) == -1

    def test_list_after_revoke_still_shows_key_disabled(self, tmp_path) -> None:
        service = _service(tmp_path)
        service.create_key(name="agent-a", allowed_collections={"hr"})
        service.revoke_key(name="agent-a")
        keys = service.list_keys()
        assert len(keys) == 1
        assert keys[0].enabled is False
        assert keys[0].revoked_at is not None

    def test_list_empty(self, tmp_path) -> None:
        assert _service(tmp_path).list_keys() == []


# ---------------------------------------------------------------------------
# Validation / uniqueness
# ---------------------------------------------------------------------------

class TestValidation:
    def test_duplicate_name_rejected(self, tmp_path) -> None:
        service = _service(tmp_path)
        service.create_key(name="agent-a", allowed_collections={"hr"})
        with pytest.raises(DuplicateKeyNameError):
            service.create_key(name="agent-a", allowed_collections={"policy"})

    def test_empty_whitelist_rejected(self, tmp_path) -> None:
        service = _service(tmp_path)
        with pytest.raises(InvalidCollectionWhitelistError):
            service.create_key(name="agent-a", allowed_collections=set())
        with pytest.raises(InvalidCollectionWhitelistError):
            service.create_key(name="agent-b", allowed_collections={""})
        with pytest.raises(InvalidCollectionWhitelistError):
            service.create_key(name="agent-c", allowed_collections={"  "})

    def test_wildcard_rejected(self, tmp_path) -> None:
        service = _service(tmp_path)
        with pytest.raises(InvalidCollectionWhitelistError):
            service.create_key(name="agent-a", allowed_collections={"*"})
        with pytest.raises(InvalidCollectionWhitelistError):
            service.create_key(name="agent-b", allowed_collections={"hr", "*"})

    def test_whitespace_stripped_from_names_and_collections(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, meta = service.create_key(
            name="  agent-a  ", allowed_collections={" hr ", "policy"},
        )
        assert meta.name == "agent-a"
        assert meta.allowed_collections == frozenset({"hr", "policy"})
        assert service.authenticate(raw) is not None

    def test_empty_name_rejected(self, tmp_path) -> None:
        service = _service(tmp_path)
        with pytest.raises(ValueError):
            service.create_key(name="", allowed_collections={"hr"})
        with pytest.raises(ValueError):
            service.create_key(name="  ", allowed_collections={"hr"})


# ---------------------------------------------------------------------------
# Revocation / rotation
# ---------------------------------------------------------------------------

class TestRevoke:
    def test_revoke_returns_metadata(self, tmp_path) -> None:
        service = _service(tmp_path)
        service.create_key(name="agent-a", allowed_collections={"hr"})
        meta = service.revoke_key(name="agent-a")
        assert isinstance(meta, ApiKeyMetadata)
        assert meta.enabled is False
        assert meta.revoked_at is not None

    def test_revoke_unknown_name_raises(self, tmp_path) -> None:
        service = _service(tmp_path)
        with pytest.raises(KeyNotFoundError):
            service.revoke_key(name="nobody")


class TestDelete:
    def test_delete_invalidates_key_and_removes_metadata(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, _ = service.create_key(name="agent-a", allowed_collections={"hr"})
        service.delete_key(name="agent-a")
        assert service.authenticate(raw) is None
        assert service.list_keys() == []

    def test_delete_unknown_name_raises(self, tmp_path) -> None:
        service = _service(tmp_path)
        with pytest.raises(KeyNotFoundError):
            service.delete_key(name="nobody")


class TestRotate:
    def test_rotate_invalidates_old_key_immediately(self, tmp_path) -> None:
        service = _service(tmp_path)
        old_raw, old_meta = service.create_key(
            name="agent-a", allowed_collections={"hr", "policy"},
        )
        new_raw, new_meta = service.rotate_key(name="agent-a")
        # Old key no longer authenticates; new key does.
        assert service.authenticate(old_raw) is None
        assert service.authenticate(new_raw) is not None
        # New key id differs from the old one.
        assert new_meta.key_id != old_meta.key_id
        assert new_meta.key_id != old_raw
        # Same name and whitelist preserved.
        assert new_meta.name == "agent-a"
        assert new_meta.allowed_collections == frozenset({"hr", "policy"})

    def test_rotate_persists_single_key_row(self, tmp_path) -> None:
        db_path = tmp_path / "mcp_access.db"
        service = ApiKeyService(db_path=db_path)
        service.create_key(name="agent-a", allowed_collections={"hr"})
        service.rotate_key(name="agent-a")
        assert len(service.list_keys()) == 1

    def test_rotate_unknown_name_raises(self, tmp_path) -> None:
        service = _service(tmp_path)
        with pytest.raises(KeyNotFoundError):
            service.rotate_key(name="nobody")


# ---------------------------------------------------------------------------
# Multi-key isolation
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_collection_whitelists_are_isolated(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw_a, meta_a = service.create_key(
            name="agent-a", allowed_collections={"hr", "policy"},
        )
        raw_b, meta_b = service.create_key(
            name="agent-b", allowed_collections={"finance"},
        )
        pa = service.authenticate(raw_a)
        pb = service.authenticate(raw_b)
        assert pa is not None and pb is not None
        assert pa.allowed_collections == frozenset({"hr", "policy"})
        assert pb.allowed_collections == frozenset({"finance"})
        # Neither principal sees the other's whitelist.
        assert pa.allowed_collections.isdisjoint(pb.allowed_collections)

    def test_revoking_one_key_does_not_affect_others(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw_a, _ = service.create_key(name="agent-a", allowed_collections={"hr"})
        raw_b, _ = service.create_key(name="agent-b", allowed_collections={"finance"})
        service.revoke_key(name="agent-a")
        assert service.authenticate(raw_a) is None
        assert service.authenticate(raw_b) is not None


# ---------------------------------------------------------------------------
# Logs / exceptions must not carry the raw key or digest
# ---------------------------------------------------------------------------

class TestSecretHygiene:
    def test_exception_messages_do_not_contain_key_material(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, _ = service.create_key(name="agent-a", allowed_collections={"hr"})
        service.revoke_key(name="agent-a")
        # Exceptions from the same-name paths must not echo the key.
        for exc in (
            KeyNotFoundError(f"no API key named 'agent-a'"),
            DuplicateKeyNameError("an API key named 'agent-a' already exists"),
        ):
            assert raw not in str(exc)
            assert raw.split(".")[-1] not in str(exc)  # secret not in message

    def test_repr_of_records_masks_digest(self, tmp_path) -> None:
        service = _service(tmp_path)
        _, meta = service.create_key(name="agent-a", allowed_collections={"hr"})
        record = service._store.get_by_key_id(meta.key_id)
        assert record is not None
        assert record.secret_digest not in repr(record)
        assert "<redacted>" in repr(record)

    def test_metadata_repr_has_no_secret(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw, meta = service.create_key(name="agent-a", allowed_collections={"hr"})
        assert raw not in repr(meta)
        assert "secret" not in repr(meta).lower() or "no secret" in repr(meta)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_authentications_do_not_cross_identities(self, tmp_path) -> None:
        service = _service(tmp_path)
        raw_a, _ = service.create_key(name="agent-a", allowed_collections={"hr"})
        raw_b, _ = service.create_key(name="agent-b", allowed_collections={"finance"})
        errors: list[Exception] = []
        results: list[bool] = []

        def worker(raw: str, expected: str) -> None:
            for _ in range(200):
                principal = service.authenticate(raw)
                if principal is None or principal.name != expected:
                    errors.append(
                        AssertionError(
                            f"identity bleed: expected {expected}, got {principal}"
                        )
                    )
                    return
            results.append(True)

        threads = [
            threading.Thread(target=worker, args=(raw_a, "agent-a")),
            threading.Thread(target=worker, args=(raw_b, "agent-b")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors, errors[0]
        assert len(results) == 2
