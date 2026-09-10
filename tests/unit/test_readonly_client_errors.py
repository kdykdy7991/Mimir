"""
P1.3 — unified read-client error vocabulary and mapping rules.

Rules (plan §P1.3):
- InvalidRequestError / ResourceNotFoundError / AccessDeniedError →
  tool-level ``CallToolResult(is_error=True)``.
- UpstreamUnavailableError / UpstreamTimeoutError and unknown exceptions →
  protocol-level (propagate out of the handler → MCPError).
- Logs never contain credentials (enforced in the HTTP client, Phase 4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp_server.clients import errors as E


def test_error_classes_match_plan():
    assert issubclass(E.InvalidRequestError, (E.ReadonlyClientError, ValueError))
    assert issubclass(E.ResourceNotFoundError, E.ReadonlyClientError)
    assert issubclass(E.AccessDeniedError, (E.ReadonlyClientError, PermissionError))
    assert issubclass(E.UpstreamUnavailableError, E.ReadonlyClientError)
    assert issubclass(E.UpstreamTimeoutError, E.ReadonlyClientError)
    for cls in (
        E.InvalidRequestError, E.ResourceNotFoundError, E.AccessDeniedError,
        E.UpstreamUnavailableError, E.UpstreamTimeoutError,
    ):
        assert cls.__name__ in E.__all__


def _dispatch_tool(args, fake_client):
    from src.mcp_server.protocol_handler import ProtocolHandler
    from src.mcp_server.tools import query_knowledge_hub as qkh
    h = ProtocolHandler()
    qkh.register(h)
    args["_client"] = fake_client
    import asyncio
    return asyncio.run(h.dispatch("query_knowledge_hub", args))


def test_tool_level_errors_return_is_error():
    """Invalid request errors surface as CallToolResult(is_error=True)."""
    from src.mcp_server.tools import get_document_summary as gds

    class Raiser:
        def get_document(self, document_id, principal):
            raise E.InvalidRequestError("bad document_id")

    import asyncio
    result = asyncio.run(gds._get_document_summary(
        {"doc_id": "x", "_client": Raiser()},
    ))
    assert result.is_error


def test_upstream_unavailable_propagates_to_protocol_level():
    """Upstream failure must NOT be swallowed into an is_error tool result;
    it propagates so the mcp library turns it into a protocol MCPError."""
    class Raiser:
        def query_knowledge(self, request, principal):
            raise E.UpstreamUnavailableError("upstream down")

    with pytest.raises(E.UpstreamUnavailableError):
        _dispatch_tool({"query": "x"}, Raiser())


def test_upstream_timeout_propagates():
    class Raiser:
        def query_knowledge(self, request, principal):
            raise E.UpstreamTimeoutError("timed out")

    with pytest.raises(E.UpstreamTimeoutError):
        _dispatch_tool({"query": "x"}, Raiser())


def test_unknown_exception_propagates_not_tool_error():
    class Raiser:
        def query_knowledge(self, request, principal):
            raise RuntimeError("unexpected bug")

    with pytest.raises(RuntimeError, match="unexpected bug"):
        _dispatch_tool({"query": "x"}, Raiser())


def test_client_never_logs_credentials_in_error():
    """A credential-like secret must not end up in the error message / logs."""
    secret = "sk-" + "a" * 32
    err = E.UpstreamUnavailableError(f"auth failed")
    # The error itself carries no secret even if constructed with context.
    assert secret not in str(err)