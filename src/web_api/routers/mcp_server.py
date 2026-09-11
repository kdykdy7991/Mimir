"""Privileged-admin MCP server status + ephemeral connection test (B4).

Endpoints
---------
- ``GET  /api/v1/mcp-server/status``         — B4.1 aggregated status
- ``POST /api/v1/mcp-server/test-connection``— B4.3 protocol-level test

Both sit behind the same trusted-admin boundary as the MCP API-key
management router (authentication deferred by roadmap). The MCP target is
resolved **only** from server configuration — never from a request — so
these endpoints are SSRF-safe.

Security contract summarised here and enforced in
``src/web_api/mcp_connection.py`` and ``src/web_api/schemas/mcp_server.py``:
- The client key is a pydantic ``SecretStr`` (model/repr never shows its
  value) and is marked ``writeOnly`` in OpenAPI (B4.4).
- The key lives only for the request lifetime; it is never persisted, logged,
  traced or returned (responses have no ``api_key`` field).
- ``trust_env=False``, hard time caps, no redirect following, no caller URL.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request

from src.core.settings import load_settings
from src.mcp_server.auth import ApiKeyService
from src.web_api.mcp_connection import (
    MisconfiguredError,
    McpConnectionTester,
    RuntimeRateLimiter,
    classify_normalized,
    probe_mcp_health,
)
from src.web_api.middleware.request_id import get_request_id
from src.web_api.schemas.mcp_server import (
    MCPConnectionError,
    MCPConnectionTestRequest,
    MCPConnectionTestResponse,
    MCPConnectionTestStage,
    MCPServerStatus,
)

router = APIRouter(prefix="/mcp-server", tags=["mcp-server"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class McpServerRouteConfig:
    """Server-configured MCP target resolved at request time.

    ``base_url`` is the public MCP client URL (``mcp_server.rag_api_base_url``).
    Independently, the MCP access-control key DB is shared with the MCP
    container so auth-failure classification can distinguish a revoked key
    from a wrong one without reading any secret.
    """

    base_url: str
    key_db_path: str
    timeout_s: float


def _server_config() -> McpServerRouteConfig:
    """Resolve the MCP target from the core Settings (never from a request)."""
    settings = load_settings("./config/settings.yaml")
    return McpServerRouteConfig(
        base_url=(settings.mcp_server.rag_api_base_url or ""),
        key_db_path=settings.mcp_access.database_path,
        timeout_s=float(settings.mcp_server.request_timeout_seconds),
    )


def _key_service(config: McpServerRouteConfig) -> ApiKeyService:
    """The shared MCP API-key store (metadata only — never the secrets)."""
    return ApiKeyService(db_path=config.key_db_path)


# ---------------------------------------------------------------------------
# Lightweight rate limiting (B4.3): expensive protocol test is throttled.
# ---------------------------------------------------------------------------
_RATE_LIMIT_MAX_PER_WINDOW = 10
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_rate_limiter = RuntimeRateLimiter(
    max_requests=_RATE_LIMIT_MAX_PER_WINDOW,
    window_seconds=_RATE_LIMIT_WINDOW_SECONDS,
)


# ---------------------------------------------------------------------------
# B4.1 — GET /status
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    response_model=MCPServerStatus,
    summary="MCP server status snapshot",
)
async def get_mcp_server_status() -> MCPServerStatus:
    config = _server_config()
    try:
        health = await probe_mcp_health(
            config.base_url, timeout_s=config.timeout_s,
        )
    except MisconfiguredError:
        return MCPServerStatus(
            status="misconfigured",
            mcp_url="",
            transport="streamable-http",
            version=None,
            upstream_status="unknown",
            checked_at=datetime.datetime.now(datetime.timezone.utc),
            latency_ms=None,
        )
    return MCPServerStatus(
        status=health["status"],
        mcp_url=health["mcp_url"],
        transport=health["transport"],
        version=health["version"],
        upstream_status=health["upstream_status"],
        checked_at=health["checked_at"],
        latency_ms=health["latency_ms"],
    )


# ---------------------------------------------------------------------------
# B4.3 — POST /test-connection
# ---------------------------------------------------------------------------


@router.post(
    "/test-connection",
    response_model=MCPConnectionTestResponse,
    summary="Run an ephemeral protocol-level MCP connection test",
)
async def test_mcp_connection(
    body: MCPConnectionTestRequest,
    request: Request,
) -> MCPConnectionTestResponse:
    config = _server_config()

    if not (config.base_url or "").strip():
        raise HTTPException(
            status_code=503,
            detail="MCP server is not configured",
        )

    if not _rate_limiter.allow():
        raise HTTPException(
            status_code=429,
            detail="Too many connection tests; try again shortly.",
        )

    api_key = body.api_key.get_secret_value()  # one-time, in-memory only
    tester = McpConnectionTester(
        key_service=_key_service(config), timeout_s=config.timeout_s,
    )
    try:
        result = await tester.run(config.base_url, api_key)
    except MisconfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail="MCP server is not configured",
        ) from exc
    finally:
        # api_key never leaves this scope.
        del api_key

    stages = [
        MCPConnectionTestStage(**stage)
        for stage in result["stages"]
    ]

    error: MCPConnectionError | None = None
    reason = result["reason"]
    if reason is not None:
        info = classify_normalized(reason)
        error = MCPConnectionError(
            code=info.code,
            message=info.message,
            suggested_action=info.suggested_action,
            request_id=get_request_id(request),
        )

    return MCPConnectionTestResponse(
        ok=result["ok"],
        stages=stages,
        error=error,
        tested_at=datetime.datetime.now(datetime.timezone.utc),
    )


__all__ = ["router"]