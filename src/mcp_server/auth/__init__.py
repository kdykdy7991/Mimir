"""
MCP HTTP access-control primitives — credential store and management CLI.

Exposes the public credential-management surface for the access-control
PRD (``docs/prd-mcp-api-key-collection-access.md``): the value objects
(:class:`AccessPrincipal`, :class:`ApiKeyMetadata`) and the
:class:`ApiKeyService` that issues, authenticates, lists, revokes and
rotates MCP API keys.

ASGI Bearer middleware, session binding and per-tool authorization live
in sibling modules (batch 2 of the PRD) and are out of scope here.
"""

from src.mcp_server.auth.key_service import ApiKeyService
from src.mcp_server.auth.models import (
    AccessPrincipal,
    ApiKeyError,
    ApiKeyMetadata,
    DuplicateKeyNameError,
    InvalidCollectionWhitelistError,
    KeyNotFoundError,
    KeyRecord,
)
from src.mcp_server.auth.store import ApiKeyStore

__all__ = [
    "AccessPrincipal",
    "ApiKeyError",
    "ApiKeyMetadata",
    "ApiKeyService",
    "ApiKeyStore",
    "DuplicateKeyNameError",
    "InvalidCollectionWhitelistError",
    "KeyNotFoundError",
    "KeyRecord",
]
