"""
``ApiKeyService`` — issue, authenticate, list, revoke and rotate MCP API keys.

Key format (PRD §4.1)::

    skdy_mcp_<key_id>.<secret>

- ``key_id`` — public random identifier (URL-safe base64, 72 bits of
  entropy) used for O(1) row lookup and security logging.
- ``secret`` — 32 random bytes URL-safe base64; shown exactly once, at
  creation/rotation time.

Only the SHA-256 digest of the secret is persisted; the raw key never
reaches the database, logs or exceptions. Authentication compares
digests with ``hmac.compare_digest`` (constant time) so timing does not
reveal how close a guess was.

Thread safety: the store opens a fresh SQLite connection per operation
(WAL), so concurrent :meth:`authenticate` calls are safe.
``last_used_at`` writes are rate-limited in-process (once per key per 5
minutes) to avoid a write per request (PRD §4.2).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from src.mcp_server.auth.models import (
    AccessPrincipal,
    ApiKeyMetadata,
    InvalidCollectionWhitelistError,
    KeyNotFoundError,
    KeyRecord,
)
from src.mcp_server.auth.store import ApiKeyStore

logger = logging.getLogger(__name__)

_KEY_PREFIX = "skdy_mcp_"
_KEY_ID_BYTES = 9        # 72 bits of entropy (requirement: >= 64)
_SECRET_BYTES = 32       # 256 bits of entropy (requirement: >= 32)
_LAST_USED_MIN_INTERVAL = 300.0  # seconds between last_used_at writes per key


def _digest(secret: str) -> str:
    """SHA-256 hex digest of a raw secret."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _generate_credential() -> tuple[str, str, str]:
    """Return ``(key_id, raw_key, secret_digest)`` for a fresh credential."""
    key_id = secrets.token_urlsafe(_KEY_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    raw_key = f"{_KEY_PREFIX}{key_id}.{secret}"
    return key_id, raw_key, _digest(secret)


def _parse_raw_key(raw_key: str) -> tuple[str, str] | None:
    """Split a presented key into ``(key_id, secret)``, or ``None`` if malformed.

    Accepts only the canonical ``skdy_mcp_<key_id>.<secret>`` shape. Both
    components are URL-safe base64 (no ``.``), so the first dot cleanly
    separates them. Unknown ``key_id`` values fail at the DB lookup.
    """
    if not isinstance(raw_key, str) or not raw_key.startswith(_KEY_PREFIX):
        return None
    body = raw_key[len(_KEY_PREFIX):]
    key_id, sep, secret = body.partition(".")
    if not sep or not key_id or not secret:
        return None
    return key_id, secret


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")


def _validate_collections(allowed_collections: set[str]) -> frozenset[str]:
    """Normalize and validate a whitelist: non-empty, no ``*`` (PRD §4.2)."""
    cleaned: set[str] = set()
    for c in allowed_collections:
        if not isinstance(c, str) or not c.strip():
            raise InvalidCollectionWhitelistError(
                f"invalid collection name: {c!r}"
            )
        cleaned.add(c.strip())
    if not cleaned:
        raise InvalidCollectionWhitelistError(
            "allowed_collections must contain at least one collection"
        )
    if "*" in cleaned:
        raise InvalidCollectionWhitelistError(
            "wildcard '*' is not supported; list collections explicitly"
        )
    return frozenset(cleaned)


class _LastUsedTracker:
    """In-process per-key throttle for ``last_used_at`` writes."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._lock = threading.Lock()
        self._interval = min_interval_seconds
        self._last: dict[str, float] = {}

    def should_update(self, key_id: str, now: float) -> bool:
        """True if enough time has passed to write ``last_used_at`` again."""
        with self._lock:
            last = self._last.get(key_id, 0.0)
            if now - last < self._interval:
                return False
            self._last[key_id] = now
            return True


def _to_metadata(record: KeyRecord) -> ApiKeyMetadata:
    """Map a store record to the secret-free public metadata shape."""
    return ApiKeyMetadata(
        key_id=record.key_id,
        name=record.name,
        allowed_collections=frozenset(record.collections),
        enabled=record.enabled,
        created_at=record.created_at,
        revoked_at=record.revoked_at,
        last_used_at=record.last_used_at,
    )


class ApiKeyService:
    """High-level credential management and authentication."""

    def __init__(
        self,
        store: ApiKeyStore | None = None,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        self._store = store or ApiKeyStore(db_path or ApiKeyStore.DEFAULT_DB_PATH)
        self._last_used = _LastUsedTracker(_LAST_USED_MIN_INTERVAL)

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def create_key(
        self,
        *,
        name: str,
        allowed_collections: set[str],
    ) -> tuple[str, ApiKeyMetadata]:
        """Create a new key and return ``(raw_key, metadata)``.

        The raw key is returned exactly once and cannot be recovered
        afterwards. Raises :class:`InvalidCollectionWhitelistError` on an
        empty/``*`` whitelist and :class:`DuplicateKeyNameError` if
        ``name`` is taken.
        """
        _validate_name(name)
        collections = _validate_collections(set(allowed_collections))
        key_id, raw_key, digest = _generate_credential()
        name = name.strip()
        now = datetime.now(timezone.utc)
        self._store.create(
            key_id=key_id,
            name=name,
            secret_digest=digest,
            collections=collections,
            created_at=now,
        )
        logger.info(
            "api_key created key_id=%s name=%s collections=%s",
            key_id, name, sorted(collections),
        )
        metadata = ApiKeyMetadata(
            key_id=key_id,
            name=name,
            allowed_collections=collections,
            enabled=True,
            created_at=now,
            revoked_at=None,
            last_used_at=None,
        )
        return raw_key, metadata

    def authenticate(self, raw_key: str) -> AccessPrincipal | None:
        """Resolve a presented key to an identity, or ``None`` if invalid.

        Returns ``None`` (never raises) for malformed keys, unknown
        key ids, revoked/disabled keys and secret mismatches, so callers
        can map every failure to the same 401 without revealing which
        credential was wrong.
        """
        parsed = _parse_raw_key(raw_key)
        if parsed is None:
            logger.debug("api_key auth failed reason=malformed")
            return None
        key_id, secret = parsed
        record = self._store.get_by_key_id(key_id)
        if record is None:
            logger.debug("api_key auth failed reason=unknown_key key_id=%s", key_id)
            return None
        if not record.enabled:
            logger.info(
                "api_key auth failed reason=disabled key_id=%s name=%s",
                key_id, record.name,
            )
            return None
        if not hmac.compare_digest(_digest(secret), record.secret_digest):
            logger.debug("api_key auth failed reason=bad_secret key_id=%s", key_id)
            return None
        if self._last_used.should_update(key_id, time.time()):
            self._store.update_last_used(key_id, datetime.now(timezone.utc))
        logger.debug(
            "api_key auth ok key_id=%s name=%s collections=%s",
            key_id, record.name, sorted(record.collections),
        )
        return AccessPrincipal(
            key_id=record.key_id,
            name=record.name,
            allowed_collections=frozenset(record.collections),
        )

    def list_keys(self) -> list[ApiKeyMetadata]:
        """All keys as secret-free metadata, ordered by creation time."""
        return [_to_metadata(record) for record in self._store.list()]

    def revoke_key(self, *, name: str) -> ApiKeyMetadata:
        """Immediately disable the named key.

        The key stays listed (with ``enabled=False`` / ``revoked_at``
        set) and fails authentication from the next request on.
        Raises :class:`KeyNotFoundError` if no such key exists.
        """
        _validate_name(name)
        record = self._store.revoke(
            name=name.strip(), revoked_at=datetime.now(timezone.utc),
        )
        logger.info(
            "api_key revoked key_id=%s name=%s", record.key_id, record.name,
        )
        return _to_metadata(record)

    def delete_key(self, *, name: str) -> None:
        """Permanently remove the named key and invalidate it immediately."""
        _validate_name(name)
        name = name.strip()
        self._store.delete(name=name)
        logger.info("api_key deleted name=%s", name)

    def rename_key(self, *, name: str, new_name: str) -> ApiKeyMetadata:
        """Rename a key without changing the credential or its access scope."""
        _validate_name(name)
        _validate_name(new_name)
        old_name, new_name = name.strip(), new_name.strip()
        record = self._store.rename(name=old_name, new_name=new_name)
        logger.info("api_key renamed key_id=%s name=%s new_name=%s", record.key_id, old_name, new_name)
        return _to_metadata(record)

    def rotate_key(self, *, name: str) -> tuple[str, ApiKeyMetadata]:
        """Issue a fresh credential for ``name``; the old key dies instantly.

        The new key keeps the same name and collection whitelist but
        gets a new ``key_id`` and secret. Returns ``(raw_key, metadata)``
        — the raw key is shown once and never again.
        Raises :class:`KeyNotFoundError` if no such key exists.
        """
        _validate_name(name)
        name = name.strip()
        existing = self._store.get_by_name(name)
        if existing is None:
            raise KeyNotFoundError(f"no API key named {name!r}")
        key_id, raw_key, digest = _generate_credential()
        now = datetime.now(timezone.utc)
        record = self._store.rotate(
            name=name,
            key_id=key_id,
            secret_digest=digest,
            collections=existing.collections,
            created_at=now,
        )
        logger.info(
            "api_key rotated key_id=%s name=%s", key_id, name,
        )
        return raw_key, _to_metadata(record)


__all__ = ["ApiKeyService"]
