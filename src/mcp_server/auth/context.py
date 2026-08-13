"""Request-scoped MCP access principals."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol


class CollectionPrincipal(Protocol):
    """Minimum identity surface required by collection authorization."""

    key_id: str
    name: str
    allowed_collections: frozenset[str]


@dataclass(frozen=True)
class TrustedLocalPrincipal:
    """Identity used solely for local stdio calls; it can read every collection."""

    key_id: str = "stdio-local"
    name: str = "stdio-local"
    allowed_collections: None = None


AccessPrincipalLike = CollectionPrincipal | TrustedLocalPrincipal

_current_principal: ContextVar[AccessPrincipalLike | None] = ContextVar(
    "skdy_mcp_access_principal", default=None,
)


def current_principal() -> AccessPrincipalLike:
    """Return the identity bound to the active MCP tool invocation."""
    principal = _current_principal.get()
    if principal is None:
        raise RuntimeError("MCP access principal is missing from request context")
    return principal


def principal_from_server_context(ctx: Any) -> AccessPrincipalLike:
    """Extract an HTTP principal or deliberately create a stdio identity."""
    request = getattr(ctx, "request", None) if ctx is not None else None
    if request is None:
        return TrustedLocalPrincipal()
    scope = getattr(request, "scope", None)
    if not isinstance(scope, dict):
        raise RuntimeError("MCP HTTP request has no ASGI scope")
    principal = scope.get("skdy.mcp.access_principal")
    if principal is None:
        raise RuntimeError("MCP HTTP request is unauthenticated")
    return principal


__all__ = [
    "AccessPrincipalLike", "CollectionPrincipal", "TrustedLocalPrincipal",
    "_current_principal", "current_principal", "principal_from_server_context",
]
