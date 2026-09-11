"""
MCP server health probe, protocol-level connection tester and rate limiter
(plan B4.1 + B4.3).

Security invariants enforced here:
- The MCP base URL is taken **only** from server configuration by the caller
  (the router). This module never accepts a caller-supplied URL.
- ``trust_env=False`` everywhere, a hard time cap, and no redirect following
  (B4.1 + B4.3). The Streamable-HTTP SDK additionally refuses cross-origin
  redirects regardless of ``follow_redirects``.
- The one-time MCP Client Key is placed **only** in the ``Authorization``
  header — never in a URL — and only lives for the duration of the request.
  It is never logged, traced or returned by this module.
- Internal exception details are recorded, at most, through the caller's
  *controlled* log path and are never placed in the user-safe diagnostics
  that B4.2 builds.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import TYPE_CHECKING, Any

import httpx
import httpx2

from src.web_api.mcp_diagnostics import (
    ConnectionDiagnostics,
    DiagnosticInfo,
    diagnostic_for,
)

if TYPE_CHECKING:
    from src.mcp_server.auth.key_service import ApiKeyService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config shape resolved by the router from core Settings.
# ---------------------------------------------------------------------------


class MisconfiguredError(Exception):
    """The MCP URL is not configured (empty base URL)."""


# ---------------------------------------------------------------------------
# B4.1 — anonymous /health probe
# ---------------------------------------------------------------------------


async def probe_mcp_health(
    base_url: str,
    *,
    timeout_s: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Probe the MCP server's anonymous ``/health`` endpoint.

    ``base_url`` is the server-configured public MCP base URL (the ``/mcp``
    endpoint lives one path segment below). Never derives from a request.

    Returns a dict compatible with :class:`MCPServerStatus`. The probe never
    touches MCP state or secrets, so the result carries no collection or key
    information.

    ``transport`` is injected by tests (``httpx.MockTransport``).
    """
    base_url = (base_url or "").strip()
    if not base_url:
        raise MisconfiguredError("mcp_server.public_base_url is not configured")

    mcp_url = f"{base_url.rstrip('/')}/mcp"
    health_url = f"{base_url.rstrip('/')}/health"

    checked_at = datetime.now(timezone.utc)
    timeout = httpx.Timeout(timeout_s)
    started = time.perf_counter()
    latency_ms: int | None = None

    async with httpx.AsyncClient(
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
        transport=transport,
    ) as client:
        try:
            resp = await client.get(health_url)
            latency_ms = int(round((time.perf_counter() - started) * 1000))
        except httpx.TimeoutException:
            return _health_result(
                status="offline", mcp_url=mcp_url,
                upstream_status="unknown", latency_ms=None,
                transport="streamable-http", version=None,
            )
        except httpx.TransportError:
            return _health_result(
                status="offline", mcp_url=mcp_url,
                upstream_status="offline", latency_ms=None,
                transport="streamable-http", version=None,
            )

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    transport_label = str(payload.get("transport") or "streamable-http")
    version = payload.get("version")

    if resp.status_code == 200 and payload.get("status") == "ok":
        # The anonymous /health only proves the MCP port answers; it does NOT
        # touch the RAG upstream (B4.1). So upstream health is reported
        # separately (probe_upstream) — never claimed "online" here.
        return _health_result(
            status="online", mcp_url=mcp_url,
            upstream_status="unknown", latency_ms=latency_ms,
            transport=transport_label, version=version,
        )

    # Reachable but not reporting healthy → degraded (never leak payload).
    return _health_result(
        status="degraded", mcp_url=mcp_url,
        upstream_status="unknown", latency_ms=latency_ms,
        transport=transport_label, version=None,
    )


async def probe_upstream(
    *,
    backend: str,
    upstream_base_url: str,
    internal_key: str,
    timeout_s: float = 5.0,
    transport: httpx.BaseTransport | None = None,
    auth_header: str = "X-API-Key",
) -> str:
    """Report the MCP Server's RAG-upstream reachability (B4.1 upstream_status).

    ``in_process`` backend shares the local process with the API, so the
    upstream is inherently online. For the ``http`` backend the probe hits the
    internal readonly API with the shared internal key (never a URL parameter,
    never logged or returned), so a wrong/absent key reads as ``degraded``
    rather than ``online`` and an unreachable API reads as ``offline``.

    Returns one of ``online|degraded|offline|unknown``.
    """
    if backend == "in_process":
        return "online"
    upstream = (upstream_base_url or "").strip()
    if not upstream:
        return "unknown"
    url = f"{upstream.rstrip('/')}/internal/mcp/v1/collections"
    headers = {}
    if internal_key:
        headers[auth_header] = internal_key
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        ) as client:
            resp = await client.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.TransportError):
        return "offline"
    if resp.status_code == 200:
        return "online"
    if resp.status_code in (401, 403):
        return "degraded"  # upstream reachable but internal auth failed
    return "degraded"


def _health_result(
    *, status: str, mcp_url: str, upstream_status: str,
    latency_ms: int | None, transport: str, version: str | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "mcp_url": mcp_url,
        "transport": transport,
        "version": version,
        "upstream_status": upstream_status,
        "checked_at": datetime.now(timezone.utc),
        "latency_ms": latency_ms,
    }


# ---------------------------------------------------------------------------
# B4.2 — normalized classification
# ---------------------------------------------------------------------------

# Internal normalized reasons → stable B4.2 code. Tools/handlers reduce any
# failure to one of these keys; ``classify`` turns it into a user-safe
# diagnostic. This indirection keeps exception internals out of the response.
_UNREACHABLE = "server_unreachable"
_TIMEOUT = "timeout"
_UNAUTHORIZED = "unauthorized"
_REVOKED = "revoked"
_HANDSHAKE = "handshake"
_INTERNAL_AUTH = "internal_auth"
_UPSTREAM_UNAVAILABLE = "upstream_unavailable"
_EMPTY_SCOPE = "empty_scope"
_UNEXPECTED = "unexpected"

_NORMALIZED_CODES: dict[str, ConnectionDiagnostics] = {
    _UNREACHABLE: ConnectionDiagnostics.SERVER_UNREACHABLE,
    _TIMEOUT: ConnectionDiagnostics.TIMEOUT,
    _UNAUTHORIZED: ConnectionDiagnostics.CLIENT_UNAUTHORIZED,
    _REVOKED: ConnectionDiagnostics.CLIENT_KEY_REVOKED,
    _HANDSHAKE: ConnectionDiagnostics.HANDSHAKE_FAILED,
    _INTERNAL_AUTH: ConnectionDiagnostics.INTERNAL_AUTH_FAILED,
    _UPSTREAM_UNAVAILABLE: ConnectionDiagnostics.UPSTREAM_UNAVAILABLE,
    _EMPTY_SCOPE: ConnectionDiagnostics.EMPTY_SCOPE,
    _UNEXPECTED: ConnectionDiagnostics.UNEXPECTED_RESPONSE,
}


def classify_normalized(reason: str) -> DiagnosticInfo:
    """Map an internal normalized reason to a user-safe diagnostic (B4.2).

    Raises ``KeyError`` on an unknown reason so call sites fail fast.
    """
    code = _NORMALIZED_CODES[reason]
    return diagnostic_for(code)


# ---------------------------------------------------------------------------
# Auth-failure resolution: revoked vs wrong key (B4.2)
# ---------------------------------------------------------------------------


def _key_id_from_raw(raw_key: str) -> str | None:
    """Best-effort public ``key_id`` for store lookup — never the secret."""
    try:
        return raw_key.split(".", 1)[0].split("_", 2)[-1]
    except Exception:  # noqa: BLE001 — classification must never raise
        return None


def resolve_auth_failure(
    raw_key: str,
    key_service: "ApiKeyService | None",
) -> DiagnosticInfo:
    """Classify a 401 from the MCP endpoint.

    For a credential that still exists but is disabled/revoked, we report
    ``client_key_revoked``; otherwise (unknown or malformed key, or wrong
    secret on an enabled key) we report ``client_unauthorized``. Only the
    *metadata* is consulted — the secret is never read or stored.
    """
    if key_service is not None:
        key_id = _key_id_from_raw(raw_key)
        if key_id is not None:
            for meta in key_service.list_keys():
                if meta.key_id == key_id:
                    if not meta.enabled:
                        return classify_normalized(_REVOKED)
                    break
    return classify_normalized(_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# Rate / concurrency limiting (B4.3)
# ---------------------------------------------------------------------------


class RuntimeRateLimiter:
    """Sliding-window rate limiter for the expensive connection test.

    ``allow()`` is thread-safe and cheap; call it before running a test. It
    never stores request content, so nothing secret passes through it.
    """

    def __init__(
        self, *, max_requests: int, window_seconds: float, clock=None,
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be > 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._clock = clock or time.monotonic
        self._events: deque[float] = deque()
        self._lock = Lock()

    def allow(self) -> bool:
        """Return True if a new test may run now, else throttle (> 429)."""
        now = self._clock()
        with self._lock:
            while self._events and now - self._events[0] > self._window_seconds:
                self._events.popleft()
            if len(self._events) >= self._max_requests:
                return False
            self._events.append(now)
            return True


# ---------------------------------------------------------------------------
# B4.3 — full protocol-level connection test
# ---------------------------------------------------------------------------

# Hardware caps for the whole test and per stage (seconds).
DEFAULT_TEST_TIMEOUT_S = 15.0
_MCP_PROTOCOL_VERSION = "2026-07-28"
_INITIALIZE_FRAME = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": _MCP_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "skdy-diagnostics", "version": "1.0.0"},
    },
}


def _stage(
    name: str, status: str, *, latency_ms: int | None = None,
    tool_count: int | None = None, collection_count: int | None = None,
) -> dict[str, Any]:
    return {
        "name": name, "status": status, "latency_ms": latency_ms,
        "tool_count": tool_count, "collection_count": collection_count,
    }


class McpConnectionTester:
    """Runs connect → initialize → tools/list → list_collections (B4.3).

    The tester always probes the server-configured MCP URL passed by the
    caller; there is no way for a request to influence the target.
    """

    def __init__(
        self, *, key_service: "ApiKeyService | None" = None,
        timeout_s: float = DEFAULT_TEST_TIMEOUT_S,
    ) -> None:
        self._key_service = key_service
        self._timeout_s = timeout_s

    async def run(self, base_url: str, api_key: str) -> dict[str, Any]:
        """Execute the test and return ``{ok, stages, reason}``.

        ``reason`` is ``None`` on success or one of the internal normalized
        keys (B4.2) that the router maps to a user-safe diagnostic. The raw
        ``api_key`` is used only for the ephemeral Authorization header.
        """
        base_url = (base_url or "").strip()
        if not base_url:
            raise MisconfiguredError("mcp_server.public_base_url is not configured")
        mcp_url = f"{base_url.rstrip('/')}/mcp"

        stages: list[dict[str, Any]] = []
        reason: str | None = None

        # -- connect + auth ------------------------------------------------
        auth_header = {"Authorization": f"Bearer {api_key}"}
        result = await self._run_connect(mcp_url, auth_header)
        stages.append(result["stage"])
        if result["ok"]:
            # -- full protocol (initialize / tools / list_collections) -----
            init = await self._run_protocol(mcp_url, auth_header)
            stages.extend(init["stages"])
            if not init["ok"]:
                reason = init["reason"]
        else:
            reason = result["reason"]
            for skipped in ("initialize", "tools_list", "list_collections"):
                stages.append(_stage(skipped, "skipped"))

        return {"ok": reason is None, "stages": stages, "reason": reason}

    # ------------------------------------------------------------------
    # connect stage: raw HTTP reachability + authentication (B4.2)
    # ------------------------------------------------------------------
    async def _run_connect(self, mcp_url: str, headers: dict[str, str]) -> dict:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._timeout_s):
                async with httpx2.AsyncClient(
                    headers=headers,
                    timeout=self._timeout_s,
                    trust_env=False,
                    follow_redirects=False,
                ) as client:
                    resp = await client.post(
                        mcp_url,
                        json=_INITIALIZE_FRAME,
                        headers={
                            "Content-Type": "application/json",
                            "Accept": "application/json, text/event-stream",
                            "mcp-protocol-version": _MCP_PROTOCOL_VERSION,
                        },
                    )
        except asyncio.TimeoutError:
            return {"ok": False, "reason": _TIMEOUT,
                    "stage": _stage("connect", "failed", latency_ms=None)}
        except httpx2.TimeoutException:
            return {"ok": False, "reason": _TIMEOUT,
                    "stage": _stage("connect", "failed", latency_ms=None)}
        except (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.RemoteProtocolError):
            return {"ok": False, "reason": _UNREACHABLE,
                    "stage": _stage("connect", "failed", latency_ms=None)}
        except httpx2.TransportError:
            return {"ok": False, "reason": _UNREACHABLE,
                    "stage": _stage("connect", "failed", latency_ms=None)}

        latency_ms = int(round((time.perf_counter() - started) * 1000))

        if resp.status_code in (401, 403):
            reason = resolve_auth_failure(
                headers["Authorization"][len("Bearer "):], self._key_service,
            ).code
            normalized = {
                ConnectionDiagnostics.CLIENT_KEY_REVOKED.value: _REVOKED,
            }.get(reason, _UNAUTHORIZED)
            return {"ok": False, "reason": normalized,
                    "stage": _stage("connect", "failed", latency_ms=latency_ms)}

        # Reached the server (any HTTP response). Deeper issues surface in
        # the initialize / tools stages.
        return {"ok": True, "reason": None,
                "stage": _stage("connect", "success", latency_ms=latency_ms)}

    # ------------------------------------------------------------------
    # initialize + tools_list + list_collections via the MCP SDK
    # ------------------------------------------------------------------
    async def _run_protocol(self, mcp_url: str, headers: dict[str, str]) -> dict:
        stages: list[dict[str, Any]] = []
        reason: str | None = None

        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        init_started = time.perf_counter()
        try:
            async with asyncio.timeout(self._timeout_s):
                http_client = httpx2.AsyncClient(
                    headers=headers,
                    timeout=self._timeout_s,
                    trust_env=False,
                    follow_redirects=False,
                )
                async with streamable_http_client(
                    mcp_url, http_client=http_client,
                ) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        # -- initialize -------------------------------------
                        init_latency = int(round(
                            (time.perf_counter() - init_started) * 1000))
                        await session.initialize()
                        stages.append(_stage(
                            "initialize", "success", latency_ms=init_latency))

                        # -- tools/list -------------------------------------
                        t_started = time.perf_counter()
                        tool_result = await session.list_tools()
                        t_latency = int(round(
                            (time.perf_counter() - t_started) * 1000))
                        tool_names = [t.name for t in tool_result.tools]
                        stages.append(_stage(
                            "tools_list", "success",
                            latency_ms=t_latency, tool_count=len(tool_names)))

                        # -- list_collections -------------------------------
                        c_started = time.perf_counter()
                        call = await session.call_tool(
                            "list_collections", arguments={},
                        )
                        c_latency = int(round(
                            (time.perf_counter() - c_started) * 1000))

                        if call.is_error:
                            stages.append(_stage(
                                "list_collections", "failed",
                                latency_ms=c_latency))
                            text = _call_error_text(call)
                            reason = _normalized_tool_error(text)
                            return {"ok": False, "stages": stages, "reason": reason}

                        count = _collection_count(call)
                        if count == 0:
                            stages.append(_stage(
                                "list_collections", "failed",
                                latency_ms=c_latency, collection_count=0))
                            reason = _EMPTY_SCOPE
                            return {"ok": False, "stages": stages, "reason": reason}

                        stages.append(_stage(
                            "list_collections", "success",
                            latency_ms=c_latency, collection_count=count))
        except asyncio.TimeoutError:
            if not stages:
                reason = _TIMEOUT
            else:
                reason = _TIMEOUT
            # Only the current in-flight stage is missing; mark it.
            return {"ok": False, "stages": stages, "reason": reason}
        except (httpx2.ConnectError, httpx2.ConnectTimeout,
                httpx2.RemoteProtocolError, httpx2.TransportError) as exc:
            logger.warning(
                "mcp protocol transport error during test-connection "
                "base_scheme=%s host_len=%d", _url_scheme(mcp_url), len(_url_host(mcp_url)),
            )
            return {
                "ok": False, "stages": stages,
                "reason": _UPSTREAM_UNAVAILABLE if stages
                else _UNREACHABLE,
            }
        except Exception as exc:  # noqa: BLE001 — internal detail stays in log
            logger.warning(
                "mcp protocol handshake/tool failure during test-connection "
                "stage_count=%d error_type=%s", len(stages), type(exc).__name__,
            )
            if not stages:
                reason = _HANDSHAKE
            else:
                # A failure after initialize is an unexpected/unclassifiable
                # server response (no credential leaked).
                reason = _UNEXPECTED
            return {"ok": False, "stages": stages, "reason": reason}

        return {"ok": True, "stages": stages, "reason": None}


def _call_error_text(call: Any) -> str:
    parts: list[str] = []
    for block in getattr(call, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return " | ".join(parts)


def _collection_count(call: Any) -> int:
    """Read the collection count from a list_collections result.

    The MCP handler returns structured content ``{"count": N, ...}`` (P2.1).
    Falls back to the human text "Collections (N)".
    """
    structured = getattr(call, "structured_content", None)
    if isinstance(structured, dict):
        try:
            return int(structured.get("count", -1))
        except (TypeError, ValueError):
            return -1
    text = _call_error_text(call)
    try:
        # "# Collections (2)" → 2
        return int(text.rsplit("(", 1)[-1].split(")", 1)[0].strip())
    except (ValueError, IndexError):
        return -1


def _normalized_tool_error(text: str) -> str:
    """Map an erroring list_collections result to a normalized B4.2 reason.

    The MCP tool returns ``CallToolResult.is_error`` for known business
    errors; its text may carry a stable internal-API code the classification
    keyed on. The credential itself never appears in this text.
    """
    lowered = text.lower()
    if "internal_unauthorized" in lowered or "missing internal api key" in lowered:
        return _INTERNAL_AUTH
    if "upstream timeout" in lowered:
        return _TIMEOUT
    if "upstream unavailable" in lowered or "internal_api_not_configured" in lowered:
        return _UPSTREAM_UNAVAILABLE
    return _UNEXPECTED


def _url_scheme(url: str) -> str:
    try:
        return url.split("://", 1)[0]
    except Exception:  # noqa: BLE001
        return "unknown"


def _url_host(url: str) -> str:
    """Best-effort host for sanitised logging — never the full URL."""
    try:
        body = url.split("://", 1)[1]
        return body.split("/", 1)[0]
    except Exception:  # noqa: BLE001
        return "<unknown>"


__all__ = [
    "DEFAULT_TEST_TIMEOUT_S",
    "McpConnectionTester",
    "MisconfiguredError",
    "RuntimeRateLimiter",
    "classify_normalized",
    "probe_mcp_health",
    "probe_upstream",
    "resolve_auth_failure",
]