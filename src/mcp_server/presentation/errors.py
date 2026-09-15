"""
Mapping of read-client errors to MCP tool-level results (Task 02.3).

Only the *new* stable codes are handled here — the legacy tools keep
their existing catch blocks and verbatim messages for
InvalidRequest / ResourceNotFound / AccessDenied so no frozen message
changes.

``rate_limited`` and ``overloaded`` surface as tool-level errors
(``CallToolResult.is_error=True``) with a machine-parseable code
prefix. They are never collapsed into an upstream outage, and Task 02
implements no actual limiting: no code path raises them except honest
upstream signalling (e.g. HTTP 429).
"""

from __future__ import annotations

from src.mcp_server.clients.errors import (
    OverloadedError,
    RateLimitedError,
)
from src.mcp_server.protocol_handler import CallToolResult, tool_error

_RATE_LIMITED_PREFIX = "rate_limited: rate limit exceeded"
_OVERLOADED_MESSAGE = "overloaded: service is overloaded, retry later"


def rate_limited_tool_result(exc: RateLimitedError) -> CallToolResult:
    message = _RATE_LIMITED_PREFIX
    if getattr(exc, "retry_after_seconds", None):
        message += f" (retry after {exc.retry_after_seconds:g} seconds)"
    return tool_error(message)


def overloaded_tool_result(exc: OverloadedError) -> CallToolResult:
    return tool_error(_OVERLOADED_MESSAGE)


def tool_result_for_new_error(exc: Exception) -> CallToolResult | None:
    """Return a tool result for rate/overload errors, else ``None``.

    ``None`` means the caller keeps its existing error handling
    (legacy tool-level errors or protocol-level propagation).
    """
    if isinstance(exc, RateLimitedError):
        return rate_limited_tool_result(exc)
    if isinstance(exc, OverloadedError):
        return overloaded_tool_result(exc)
    return None


__all__ = [
    "rate_limited_tool_result",
    "overloaded_tool_result",
    "tool_result_for_new_error",
]
