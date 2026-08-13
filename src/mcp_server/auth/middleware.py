"""
``MCPAccessAuthMiddleware`` — Starlette Bearer API-key authentication.

Implements PRD §5: every request to the ``streamable-http`` MCP endpoint
must present ``Authorization: Bearer skdy_mcp_<key_id>.<secret>``. The
middleware is mounted *outside* the MCP ASGI app so it runs before the
JSON-RPC session manager, on every POST/GET/DELETE.

Responsibilities:

- Parse the ``Authorization`` header (``Bearer`` scheme only).
- Resolve the key via :class:`ApiKeyService.authenticate`.
- On any failure return 401 + ``WWW-Authenticate: Bearer`` with the same
  opaque ``{"error":"unauthorized"}`` body — the PRD requires not leaking
  whether the key exists, was revoked, or was malformed.
- On success inject the authenticated identity into the ASGI scope under
  two keys, for two distinct consumers:

  - ``skdy.mcp.access_principal`` — the :class:`AccessPrincipal` that
    ``src.mcp_server.auth.context.principal_from_server_context`` reads
    and that the tools authorize against.
  - ``user`` — an ``mcp`` library :class:`AuthenticatedUser` whose
    ``AuthorizationContext`` the session manager compares against the
    owner that created the session. This is what turns cross-key session
    reuse into a rejection, for free (PRD §5.2).

- ``GET /health`` stays anonymous (it never reaches this middleware).

The raw key never enters logs: only ``key_id`` and failure *reason*
(without the credential) are logged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.mcp_server.auth.key_service import ApiKeyService

if TYPE_CHECKING:
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# ASGI scope keys injected by this middleware.
SCOPE_PRINCIPAL = "skdy.mcp.access_principal"
"""Scope key holding the :class:`AccessPrincipal` for this request."""

_UNAUTHORIZED_BODY = {"error": "unauthorized"}
_WWW_AUTHENTICATE = "Bearer"


def _authorization_header_ok(request: Request) -> bool:
    """Reject anything that is not exactly ``Bearer <key>``."""
    header = request.headers.get("authorization", "")
    return header.lower().startswith("bearer ") and len(header) > len("bearer ")


class MCPAccessAuthMiddleware(BaseHTTPMiddleware):
    """Require a valid MCP API key on the MCP endpoint."""

    def __init__(
        self, app: "ASGIApp", *, key_service: ApiKeyService, health_path: str = "/health",
    ) -> None:
        super().__init__(app)
        self._key_service = key_service
        self._health_path = "/" + health_path.strip("/")

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> JSONResponse:
        # Health probes stay anonymous (PRD §5.1). The middleware is
        # mounted app-wide; only the MCP endpoint requires a key.
        if request.url.path.rstrip("/") == self._health_path:
            return await call_next(request)

        if not _authorization_header_ok(request):
            logger.debug(
                "mcp_access auth rejected reason=missing_or_malformed_header "
                "path=%s method=%s",
                request.url.path, request.method,
            )
            return self._unauthorized()

        raw_key = request.headers["authorization"][len("Bearer "):]
        principal = self._key_service.authenticate(raw_key)
        if principal is None:
            # Uniform external response; the specific reason is in the log.
            logger.info(
                "mcp_access auth rejected key_id=%s path=%s method=%s",
                _key_id_from_raw(raw_key), request.url.path, request.method,
            )
            return self._unauthorized()

        # Inject the application principal for the tools …
        request.scope[SCOPE_PRINCIPAL] = principal
        # … and the mcp-library identity so the session manager's native
        # session-owner check treats this key as the session's owner.
        from mcp.server.auth.middleware.bearer_auth import (
            AccessToken,
            AuthenticatedUser,
            authorization_context,
        )

        token = AccessToken(
            token=raw_key,
            client_id=principal.key_id,
            scopes=["mcp.read"],
            subject=principal.key_id,
            claims={"name": principal.name, "key_id": principal.key_id},
        )
        user = AuthenticatedUser(token)
        request.scope["user"] = user
        request.scope["auth"] = type(
            "AuthCredentials", (), {"scopes": ["mcp.read"]},
        )()
        request.scope["skdy.mcp.authorization_context"] = authorization_context(user)

        logger.debug(
            "mcp_access auth ok key_id=%s name=%s collections=%s path=%s",
            principal.key_id, principal.name,
            sorted(principal.allowed_collections), request.url.path,
        )
        return await call_next(request)

    @staticmethod
    def _unauthorized() -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content=_UNAUTHORIZED_BODY,
            headers={"WWW-Authenticate": _WWW_AUTHENTICATE},
        )


def _key_id_from_raw(raw_key: str) -> str:
    """Best-effort public key_id for logs — never the full key."""
    try:
        return raw_key.split(".", 1)[0].split("_", 2)[-1]
    except Exception:  # noqa: BLE001 — logging must never raise
        return "<unparseable>"


__all__ = ["MCPAccessAuthMiddleware", "SCOPE_PRINCIPAL"]
