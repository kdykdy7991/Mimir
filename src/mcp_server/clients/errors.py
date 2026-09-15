"""
Unified read error vocabulary (P1.3).

The in-process and HTTP clients raise these so the MCP handlers can map
them consistently:

- :class:`InvalidRequestError`, :class:`ResourceNotFoundError`,
  :class:`AccessDeniedError` → tool-level ``CallToolResult(is_error=True)``.
- :class:`UpstreamUnavailableError`, :class:`UpstreamTimeoutError` (and any
  unknown exception) → protocol-level error (raised through, becomes an
  MCPError). Logs must not contain credentials.

These names mirror the plan §P1.3 and keep the clients free of MCP content
types.
"""

from __future__ import annotations


class ReadonlyClientError(Exception):
    """Base class for all read-client errors."""


class InvalidRequestError(ReadonlyClientError, ValueError):
    """The caller supplied an invalid request (e.g. bad pagination args)."""


class ResourceNotFoundError(ReadonlyClientError):
    """A requested resource does not exist or is not accessible."""


class AccessDeniedError(ReadonlyClientError, PermissionError):
    """The principal is not permitted to access the resource."""


class UpstreamUnavailableError(ReadonlyClientError):
    """The upstream (in-process stack or remote HTTP API) is unavailable."""


class UpstreamTimeoutError(ReadonlyClientError):
    """The upstream call exceeded its time budget."""


class RateLimitedError(ReadonlyClientError):
    """A caller (or upstream) rate budget was exceeded.

    Task 02 establishes the contract/type only — the project implements
    no limiter yet. It must never be collapsed into
    :class:`UpstreamUnavailableError` (a budget rejection is not an
    infrastructure outage).
    """

    def __init__(self, message: str = "", *, retry_after_seconds: float | None = None):
        super().__init__(message or "rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class OverloadedError(ReadonlyClientError):
    """The service is at capacity (contract in Task 02; no overload
    shedding is implemented yet)."""


__all__ = [
    "ReadonlyClientError",
    "InvalidRequestError",
    "ResourceNotFoundError",
    "AccessDeniedError",
    "UpstreamUnavailableError",
    "UpstreamTimeoutError",
    "RateLimitedError",
    "OverloadedError",
]