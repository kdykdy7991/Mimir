"""
Credential and access-control models for MCP HTTP authentication.

Defines the public, frozen value objects that flow between the
API-key store/service layer and the (batch-2) HTTP authentication layer:

- :class:`AccessPrincipal` — the authenticated identity injected into
  the ASGI request scope for a ``streamable-http`` request.
- :class:`ApiKeyMetadata` — a key's non-secret metadata, safe to return
  from ``list_keys`` / ``revoke_key`` and safe to log.
- :class:`KeyRecord` — the store's internal row record (includes the
  secret digest). Internal to the auth package; the raw secret never
  enters the model layer.

No model here ever holds a raw secret. :class:`ApiKeyMetadata` and
:class:`AccessPrincipal` deliberately have no secret/digest fields, so
callers cannot accidentally leak them into logs or HTTP responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# ---------------------------------------------------------------------------
# Public value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AccessPrincipal:
    """The authenticated identity bound to a ``streamable-http`` request.

    Immutable so it can be stored on the ASGI request scope and shared
    across threads without identity bleed (PRD §5.1).
    """

    key_id: str
    name: str
    allowed_collections: frozenset[str]


@dataclass(frozen=True)
class ApiKeyMetadata:
    """Non-secret metadata for one API key.

    Contains neither the secret nor its digest, so it is safe to
    serialize, return from ``list_keys`` and log.
    """

    key_id: str
    name: str
    allowed_collections: frozenset[str]
    enabled: bool
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


# ---------------------------------------------------------------------------
# Internal row record (store <-> service)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KeyRecord:
    """A full database row as read by :class:`ApiKeyStore`.

    Internal to the auth package. Carries ``secret_digest``; only
    :class:`ApiKeyService` holds it, and the service never logs or
    returns it. The ``__repr__`` masks the digest so a stray log of a
    ``KeyRecord`` cannot leak it.
    """

    key_id: str
    name: str
    secret_digest: str
    enabled: bool
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    collections: frozenset[str]

    def __repr__(self) -> str:
        return (
            "KeyRecord("
            f"key_id={self.key_id!r}, name={self.name!r}, "
            "secret_digest=<redacted>, "
            f"enabled={self.enabled!r}, "
            f"created_at={self.created_at!r}, "
            f"revoked_at={self.revoked_at!r}, "
            f"last_used_at={self.last_used_at!r}, "
            f"collections={sorted(self.collections)!r})"
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ApiKeyError(Exception):
    """Base class for API-key store/service failures."""


class DuplicateKeyNameError(ApiKeyError):
    """A key with this ``name`` already exists."""


class KeyNotFoundError(ApiKeyError):
    """No key exists with the requested ``name``."""


class InvalidCollectionWhitelistError(ValueError):
    """The collection whitelist is empty or uses the unsupported ``*``."""


__all__ = [
    "AccessPrincipal",
    "ApiKeyError",
    "ApiKeyMetadata",
    "DuplicateKeyNameError",
    "InvalidCollectionWhitelistError",
    "KeyNotFoundError",
    "KeyRecord",
]
