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
    """Return the identity bound to the active MCP tool invocation.

    When no principal is bound — a stdio/direct call, or a tool handler
    invoked outside :meth:`ProtocolHandler.dispatch` (e.g. unit tests) —
    fall back to :class:`TrustedLocalPrincipal`. That matches the PRD §7
    stdio strategy (local calls read every collection). The HTTP
    fail-closed guarantee lives at the dispatch boundary: for
    ``streamable-http`` the principal is always resolved first by
    :func:`principal_from_server_context`, which raises when a request is
    unauthenticated, so this fallback can never let an HTTP request slip
    through as trusted.
    """
    principal = _current_principal.get()
    if principal is None:
        return TrustedLocalPrincipal()
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
