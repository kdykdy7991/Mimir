"""DTOs for the privileged-admin MCP server status + connectivity test surface.

These endpoints sit behind the same trusted-admin boundary as the MCP
API-key management router — no credential is ever stored or returned.

Security contract for :class:`MCPConnectionTestRequest`:
- ``api_key`` is a pydantic :class:`SecretStr`, so the model ``str`` /
  ``repr`` never shows the value.
- It is marked ``writeOnly`` in the OpenAPI schema (B4.4).
- Request size is bounded via a hard ``max_length``.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field, SecretStr

from src.web_api.schemas._types import UtcDatetime

_SERVER_STATUS_VALUES = ("online", "degraded", "offline", "misconfigured")
_UPSTREAM_STATUS_VALUES = ("online", "offline", "unknown")
_STAGE_NAME_VALUES = ("connect", "initialize", "tools_list", "list_collections")
_STAGE_STATUS_VALUES = ("success", "failed", "skipped")

MCP_TEST_API_KEY_MAX_LENGTH = 256
"""Hard request-size bound for the one-time MCP Client Key (B4.3)."""


class MCPServerStatus(BaseModel):
    """B4.1 aggregated status snapshot of the MCP server.

    ``mcp_url`` is the *public* client URL (from server config) — never a
    container-internal address or a caller-supplied URL. No collection or
    key information is included.
    """

    status: str = Field(..., description="Enum: online|degraded|offline|misconfigured")
    mcp_url: str
    transport: str = Field(
        default="streamable-http",
        description="MCP transport as reported by the anonymous /health probe.",
    )
    version: str | None = Field(
        default=None,
        description="Server-reported MCP version (if the server exposes one).",
    )
    upstream_status: str = Field(
        default="unknown",
        description="Enum: online|offline|unknown — main RAG API reachability.",
    )
    checked_at: UtcDatetime
    latency_ms: int | None = Field(
        default=None,
        description="Health-probe round-trip latency in milliseconds.",
    )


class MCPConnectionTestRequest(BaseModel):
    """POST body for the ephemeral connection test (B4.3).

    ``api_key`` is a one-time MCP Client Key. It exists only for the
    request lifetime; nothing is persisted, logged, traced or echoed.
    """

    api_key: SecretStr = Field(
        ...,
        min_length=1,
        max_length=MCP_TEST_API_KEY_MAX_LENGTH,
        description=(
            "The MCP Client Key returned at create/rotate time. It is "
            "send-only: never stored, logged or returned."
        ),
        json_schema_extra={"writeOnly": True, "format": "password"},
    )


class MCPConnectionTestStage(BaseModel):
    """One protocol-level stage of the connection test (B4.3)."""

    name: str = Field(..., description="connect|initialize|tools_list|list_collections")
    status: str = Field(..., description="success|failed|skipped")
    latency_ms: int | None = Field(
        default=None,
        description="Wall-clock time for the stage in milliseconds.",
    )
    tool_count: int | None = Field(
        default=None,
        description="Number of tools advertised by the server (tools_list only).",
    )
    collection_count: int | None = Field(
        default=None,
        description="Number of authorized collections (list_collections only).",
    )


class MCPConnectionError(BaseModel):
    """User-safe diagnostics for a failed test (B4.2).

    ``message`` and ``suggested_action`` are always safe for end-users; the
    raw ``code`` is the stable contract the frontend branches on.
    """

    code: str
    message: str
    suggested_action: str
    request_id: str | None = Field(
        default=None,
        description="Server request id, useful when reporting an issue.",
    )


class MCPConnectionTestResponse(BaseModel):
    """Result of the full protocol test (B4.3).

    ``ok`` is True only when every stage succeeded. ``error`` is null on
    success and holds a user-safe diagnostics object otherwise. The raw
    ``api_key`` is never a field here.
    """

    ok: bool
    stages: list[MCPConnectionTestStage]
    error: MCPConnectionError | None = Field(
        default=None,
        description="User-safe diagnostics; null when ok is True.",
    )
    tested_at: UtcDatetime


__all__ = [
    "MCP_TEST_API_KEY_MAX_LENGTH",
    "MCPConnectionError",
    "MCPConnectionTestRequest",
    "MCPConnectionTestResponse",
    "MCPConnectionTestStage",
    "MCPServerStatus",
    "_SERVER_STATUS_VALUES",
    "_STAGE_NAME_VALUES",
    "_STAGE_STATUS_VALUES",
    "_UPSTREAM_STATUS_VALUES",
]