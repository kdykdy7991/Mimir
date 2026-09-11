"""
MCP connection-diagnostics classification (plan B4.2).

Every failure a privileged-admin MCP status / connection test can hit is
mapped onto one of a small, stable set of internal :class:`ConnectionDiagnostics`
codes. Each code carries a *user-safe* ``message`` and ``suggested_action``:
no internal exception text, no file paths, no credential material is ever
placed into either string. Full internal stack detail, if we ever log it,
goes to the controlled server log *after* Key/Authorization-header redaction
(see ``src/web_api/observability.py`` / the request-timing guard in B4.4).

The ``code`` strings are stable contract values the Web UI branches on (see
``web/src/features/system/mcp-diagnostics-guidance.ts``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConnectionDiagnostics(str, Enum):
    """Stable codes for MCP connectivity-test failures (B4.2)."""

    SERVER_UNREACHABLE = "server_unreachable"
    HANDSHAKE_FAILED = "handshake_failed"
    CLIENT_UNAUTHORIZED = "client_unauthorized"
    CLIENT_KEY_REVOKED = "client_key_revoked"
    INTERNAL_AUTH_FAILED = "internal_auth_failed"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    EMPTY_SCOPE = "empty_scope"
    TIMEOUT = "timeout"
    UNEXPECTED_RESPONSE = "unexpected_response"


@dataclass(frozen=True)
class DiagnosticInfo:
    """User-safe message + suggested_action for a diagnostics code."""

    code: str
    message: str
    suggested_action: str


# ---------------------------------------------------------------------------
# User-safe copy. Keep in sync with
# ``web/src/features/system/mcp-diagnostics-guidance.ts``. These strings are
# deliberately generic — they never carry an exception, path or credential.
# ---------------------------------------------------------------------------
_DIAGNOSTIC_MAP: dict[ConnectionDiagnostics, DiagnosticInfo] = {
    ConnectionDiagnostics.SERVER_UNREACHABLE: DiagnosticInfo(
        code=ConnectionDiagnostics.SERVER_UNREACHABLE.value,
        message="无法连接到 MCP Server。",
        suggested_action="检查 MCP 容器是否运行，以及管理台配置的公开 MCP URL 是否可达。",
    ),
    ConnectionDiagnostics.HANDSHAKE_FAILED: DiagnosticInfo(
        code=ConnectionDiagnostics.HANDSHAKE_FAILED.value,
        message="MCP 协议握手失败。",
        suggested_action="确认服务端与客户端使用兼容的 Streamable HTTP MCP 协议版本。",
    ),
    ConnectionDiagnostics.CLIENT_UNAUTHORIZED: DiagnosticInfo(
        code=ConnectionDiagnostics.CLIENT_UNAUTHORIZED.value,
        message="MCP Client Key 未通过鉴权。",
        suggested_action="请使用创建或轮换时返回的 MCP Client Key，而不是服务内部共享凭证。",
    ),
    ConnectionDiagnostics.CLIENT_KEY_REVOKED: DiagnosticInfo(
        code=ConnectionDiagnostics.CLIENT_KEY_REVOKED.value,
        message="该 MCP Client Key 已被撤销。",
        suggested_action="该 Key 已撤销，请创建或轮换 Key 后更新客户端配置。",
    ),
    ConnectionDiagnostics.INTERNAL_AUTH_FAILED: DiagnosticInfo(
        code=ConnectionDiagnostics.INTERNAL_AUTH_FAILED.value,
        message="MCP Server 已连上，但内部 RAG API 鉴权失败。",
        suggested_action="请检查主服务与 MCP 容器注入的内部共享凭证是否完全一致。",
    ),
    ConnectionDiagnostics.UPSTREAM_UNAVAILABLE: DiagnosticInfo(
        code=ConnectionDiagnostics.UPSTREAM_UNAVAILABLE.value,
        message="MCP Server 在线，但主 RAG API 不可用。",
        suggested_action="MCP Server 在线，但主 RAG API 不可用，请检查 API 容器健康状态。",
    ),
    ConnectionDiagnostics.EMPTY_SCOPE: DiagnosticInfo(
        code=ConnectionDiagnostics.EMPTY_SCOPE.value,
        message="该 MCP Client Key 没有知识库访问权限。",
        suggested_action="该 Key 没有知识库权限，请编辑 Key 并至少选择一个知识库。",
    ),
    ConnectionDiagnostics.TIMEOUT: DiagnosticInfo(
        code=ConnectionDiagnostics.TIMEOUT.value,
        message="连接或请求超时。",
        suggested_action="检查反向代理、网络连通性和服务负载后重试。",
    ),
    ConnectionDiagnostics.UNEXPECTED_RESPONSE: DiagnosticInfo(
        code=ConnectionDiagnostics.UNEXPECTED_RESPONSE.value,
        message="MCP Server 返回了无法识别的响应。",
        suggested_action="服务返回了无法识别的响应，请携带 Request ID 检查服务日志。",
    ),
}


def diagnostic_for(code: ConnectionDiagnostics | str) -> DiagnosticInfo:
    """Return the user-safe message/action for a diagnostics code.

    Raises ``KeyError`` for an unknown code so a typo in a new call-site
    fails fast (tests catch it) rather than silently degrades to a
    misleading generic message.
    """
    if isinstance(code, str):
        code = ConnectionDiagnostics(code)
    return _DIAGNOSTIC_MAP[code]


def all_diagnostics() -> list[DiagnosticInfo]:
    """All codes in a stable order — used by tests to pin the mapping."""
    for code in ConnectionDiagnostics:
        yield _DIAGNOSTIC_MAP[code]


__all__ = [
    "ConnectionDiagnostics",
    "DiagnosticInfo",
    "all_diagnostics",
    "diagnostic_for",
]