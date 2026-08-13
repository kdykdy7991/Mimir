"""Collection-level authorization shared by all MCP read tools."""

from __future__ import annotations

from collections.abc import Iterable

from src.mcp_server.auth.context import AccessPrincipalLike, TrustedLocalPrincipal


class CollectionAccessDenied(PermissionError):
    """Raised when a principal requests a collection outside its grant."""


class CollectionSelectionRequired(ValueError):
    """Raised when a multi-collection credential omitted ``collection``."""


def can_access_collection(principal: AccessPrincipalLike, collection: str) -> bool:
    """Return whether a principal may read the named collection."""
    if isinstance(principal, TrustedLocalPrincipal):
        return True
    return collection in principal.allowed_collections


def require_collection_access(principal: AccessPrincipalLike, collection: str) -> None:
    """Require access without exposing the principal's collection grants."""
    if not can_access_collection(principal, collection):
        raise CollectionAccessDenied("collection is not accessible with this credential")


def resolve_query_collection(
    principal: AccessPrincipalLike,
    requested: str | None,
    *,
    legacy_default: str = "default",
) -> str:
    """Choose and authorize a collection for a query tool invocation."""
    if requested:
        require_collection_access(principal, requested)
        return requested
    if isinstance(principal, TrustedLocalPrincipal):
        return legacy_default
    if len(principal.allowed_collections) == 1:
        return next(iter(principal.allowed_collections))
    raise CollectionSelectionRequired(
        "'collection' is required when this credential can access multiple "
        "knowledge bases",
    )


def filter_accessible_collections(
    principal: AccessPrincipalLike,
    collections: Iterable[str],
) -> list[str]:
    """Filter a server-side collection listing without leaking other names."""
    return [item for item in collections if can_access_collection(principal, item)]


__all__ = [
    "CollectionAccessDenied", "CollectionSelectionRequired", "can_access_collection",
    "filter_accessible_collections", "require_collection_access", "resolve_query_collection",
]
