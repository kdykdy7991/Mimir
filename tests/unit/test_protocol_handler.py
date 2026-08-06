"""
Unit tests for ProtocolHandler (E2) — tool registry + dispatch.

Covers:
- Registration / unregistration / name listing
- Input validation (empty name, non-callable handler)
- Server construction: list_tools reflects the registry
- call_tool routing: known name → handler result
- Error convention (MCP 2.0): known errors return
  ``CallToolResult(is_error=True)`` (survives dispatch → server);
  unknown tool raises; unexpected handler exceptions propagate
  (→ protocol MCPError at the library boundary)
- Result normalisation: str, list, tuple[str, dict], CallToolResult, bad types
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.server import Server
from mcp.types import CallToolResult, TextContent

from src.mcp_server.protocol_handler import (
    ProtocolHandler,
    _normalize_result,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _echo_handler(args: dict[str, Any]) -> dict[str, Any]:
    return {"received": args}


async def _string_handler(args: dict[str, Any]) -> str:
    return f"echo: {args.get('x', '')}"


async def _list_handler(args: dict[str, Any]) -> list[TextContent]:
    return [TextContent(type="text", text="from-list")]


async def _tuple_handler(args: dict[str, Any]) -> tuple[str, dict]:
    return ("markdown body", {"key": "value", "n": 3})


async def _bad_type_handler(args: dict[str, Any]) -> int:
    return 42  # not a supported result type


async def _boom(args: dict[str, Any]) -> None:
    raise RuntimeError("simulated handler explosion")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_basic():
    h = ProtocolHandler()
    h.register(
        name="t1", description="d", input_schema={}, handler=_echo_handler,
    )
    assert h.has("t1")
    assert h.list_names() == ["t1"]


def test_register_re_register_overwrites():
    h = ProtocolHandler()

    async def h1(args): return "v1"
    async def h2(args): return "v2"

    h.register(name="t", description="", input_schema={}, handler=h1)
    h.register(name="t", description="new desc", input_schema={}, handler=h2)
    assert h.get("t").description == "new desc"


def test_register_rejects_empty_name():
    h = ProtocolHandler()
    with pytest.raises(ValueError):
        h.register(name="", description="", input_schema={}, handler=_echo_handler)


def test_register_rejects_non_callable_handler():
    h = ProtocolHandler()
    with pytest.raises(TypeError):
        h.register(name="t", description="", input_schema={}, handler=42)  # type: ignore


def test_unregister_returns_true_when_present():
    h = ProtocolHandler()
    h.register(name="t", description="", input_schema={}, handler=_echo_handler)
    assert h.unregister("t") is True
    assert h.has("t") is False


def test_unregister_returns_false_when_absent():
    h = ProtocolHandler()
    assert h.unregister("nope") is False


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------

def test_build_server_returns_mcp_server():
    h = ProtocolHandler()
    h.register(name="t", description="d", input_schema={}, handler=_echo_handler)
    server = h.build_server()
    assert isinstance(server, Server)


def test_build_server_empty_registry_is_valid():
    h = ProtocolHandler()
    server = h.build_server()
    assert isinstance(server, Server)


# ---------------------------------------------------------------------------
# Result normalisation (unit-level)
# ---------------------------------------------------------------------------

def test_normalize_str():
    out = _normalize_result("t", "hello")
    assert len(out) == 1
    assert isinstance(out[0], TextContent)
    assert out[0].text == "hello"


def test_normalize_list_passthrough():
    blocks = [TextContent(type="text", text="a")]
    out = _normalize_result("t", blocks)
    assert out is not blocks
    assert out[0].text == "a"


def test_normalize_tuple_returns_pair():
    """The mcp library understands (unstructured, structured) tuples
    and routes the two halves to content / structuredContent."""
    unstructured, structured = _normalize_result("t", ("md", {"foo": 1}))
    assert isinstance(unstructured, list)
    assert len(unstructured) == 1
    assert unstructured[0].text == "md"
    assert structured == {"foo": 1}


def test_normalize_tuple_rejects_non_dict_structured():
    with pytest.raises(TypeError):
        _normalize_result("t", ("md", ["not", "a", "dict"]))


def test_normalize_rejects_unsupported_type():
    with pytest.raises(TypeError):
        _normalize_result("t", 42)


def test_normalize_tuple_with_empty_structured():
    unstructured, structured = _normalize_result("t", ("md", {}))
    assert unstructured[0].text == "md"
    assert structured == {}


# ---------------------------------------------------------------------------
# call_tool dispatch (asyncio)
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_dispatch_dispatches_to_handler():
    h = ProtocolHandler()
    # echo returns a dict — not a supported shape, so this will fail
    # normalisation. Use it to confirm dispatch routes correctly.
    h.register(name="echo", description="", input_schema={}, handler=_echo_handler)
    with pytest.raises(TypeError, match="unsupported return type"):
        _run(h.dispatch("echo", {"x": "hi"}))


def test_dispatch_with_string_return():
    h = ProtocolHandler()
    h.register(name="s", description="", input_schema={}, handler=_string_handler)
    result = _run(h.dispatch("s", {"x": "abc"}))
    assert len(result) == 1
    assert result[0].text == "echo: abc"


def test_dispatch_with_list_return():
    h = ProtocolHandler()
    h.register(name="l", description="", input_schema={}, handler=_list_handler)
    result = _run(h.dispatch("l", {}))
    assert result[0].text == "from-list"


def test_dispatch_with_tuple_return():
    h = ProtocolHandler()
    h.register(name="tup", description="", input_schema={}, handler=_tuple_handler)
    result = _run(h.dispatch("tup", {}))
    # dispatch returns the (unstructured, structured) tuple as-is;
    # the mcp library wraps it into a CallToolResult on the wire.
    assert isinstance(result, tuple)
    assert len(result) == 2
    unstructured, structured = result
    assert unstructured[0].text == "markdown body"
    assert structured == {"key": "value", "n": 3}


def test_dispatch_handler_exception_propagates():
    """Unexpected handler exceptions bubble up — the mcp 2.0 library
    converts them to a protocol-level MCPError. Known errors should be
    returned as CallToolResult(is_error=True) instead (see the module
    docstring's "Error mapping")."""
    h = ProtocolHandler()
    h.register(name="boom", description="", input_schema={}, handler=_boom)
    with pytest.raises(RuntimeError, match="simulated handler explosion"):
        _run(h.dispatch("boom", {}))


def test_dispatch_unknown_tool_raises():
    h = ProtocolHandler()
    with pytest.raises(ValueError, match="Unknown tool"):
        _run(h.dispatch("nope", {}))


def test_dispatch_bad_return_type_raises():
    h = ProtocolHandler()
    h.register(name="bad", description="", input_schema={}, handler=_bad_type_handler)
    with pytest.raises(TypeError, match="unsupported return type"):
        _run(h.dispatch("bad", {}))


# ---------------------------------------------------------------------------
# build_server wires the dispatch method correctly
# ---------------------------------------------------------------------------

def test_build_server_call_tool_uses_dispatch():
    """The Server's tools/call handler should delegate to dispatch()."""
    h = ProtocolHandler()
    h.register(name="s", description="", input_schema={}, handler=_string_handler)
    server = h.build_server()
    # MCP 2.0: request handlers are keyed by method string.
    entry = server.get_request_handler("tools/call")
    assert entry is not None, "tools/call handler not registered"
    from mcp.types import CallToolRequest, CallToolRequestParams
    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="s", arguments={"x": "wrapped"}),
    )
    result = _run(entry.handler(ctx=None, params=request.params))
    # MCP 2.0 returns a CallToolResult directly (no ServerResult wrapper).
    assert isinstance(result, CallToolResult)
    assert result.content[0].text == "echo: wrapped"


def test_build_server_preserves_tool_is_error():
    """A tool returning CallToolResult(is_error=True) keeps the flag
    through dispatch → build_server (known errors, not protocol errors)."""
    from src.mcp_server.protocol_handler import tool_error

    h = ProtocolHandler()
    async def bad(args):
        return tool_error("boom: document not found")
    h.register(name="bad", description="", input_schema={}, handler=bad)
    server = h.build_server()
    entry = server.get_request_handler("tools/call")
    from mcp.types import CallToolRequest, CallToolRequestParams
    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="bad", arguments={}),
    )
    result = _run(entry.handler(ctx=None, params=request.params))
    assert result.is_error
    assert "boom" in result.content[0].text
