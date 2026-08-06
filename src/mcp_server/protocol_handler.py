"""
Protocol Handler (E2) — tool registry and dispatch layer.

Thin wrapper over the ``mcp`` library's :class:`Server` class. Holds
the catalogue of registered tools (name + JSON Schema + handler) and
exposes them through an MCP-compatible server instance.

Why not hand-rolled JSON-RPC?
-----------------------------
The DEV_SPEC E2 originally described implementing JSON-RPC 2.0
parsing from scratch. In practice, the ``mcp`` library already
implements MCP/JSON-RPC 2.0 correctly and is what every official MCP
client (GitHub Copilot, Claude Desktop, Cursor) speaks against. A
hand-rolled implementation would have to mirror its wire format
exactly to stay compatible. So this module is a *registry*, not a
protocol implementation — protocol correctness is delegated to
``mcp.server.Server``.

Transport compatibility
-----------------------
``build_server()`` constructs an :class:`mcp.server.Server` using
the mcp v2 constructor API (``on_list_tools`` /
``on_call_tool`` kwargs). The returned server is transport-agnostic
and can be driven by:

- ``mcp.server.stdio.stdio_server()`` (default CLI mode)
- :class:`mcp.server.streamable_http_manager.StreamableHTTPSessionManager`
  (HTTP / Streamable-HTTP transport for remote clients)

Both paths share the same tool catalogue and dispatch logic, so
they cannot drift apart.

Error mapping (MCP 2.0)
-----------------------
Two distinct error surfaces, kept deliberately separate:

- **Known parameter / business errors** (empty ``query``, missing
  ``doc_id``, document not found): the tool handler **returns** a
  :class:`CallToolResult` with ``is_error=True`` (see :func:`tool_error`).
  The MCP spec reserves ``CallToolResult.is_error`` for tool-level
  failures so the client can inspect the message and continue the
  conversation. Returning the result (rather than raising) is what keeps
  it a tool result instead of a protocol error.

- **Unexpected exceptions** (bugs, upstream crashes): handlers let them
  propagate. The mcp 2.0 library converts a raised handler exception
  into a **protocol-level MCPError** (JSON-RPC error), which the client
  surfaces as an exception. We do NOT swallow those — a genuine crash
  should not masquerade as a tool result.

- **Unknown tool name**: :meth:`ProtocolHandler.dispatch` raises
  ``ValueError`` → the library turns it into a protocol-level MCPError.

So the boundary is: *"I know why this failed"* → ``is_error`` result;
*"I don't know what happened"* or *"you asked for something that isn't a
tool"* → protocol MCPError.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from mcp.server import Server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ImageContent,
    ListToolsResult,
    TextContent,
    Tool,
)

logger = logging.getLogger(__name__)


# A tool handler is an async callable that takes a dict of arguments
# and returns one of:
#   - str  → wrapped in a single TextContent result
#   - list[TextContent | ImageContent | ...]  → returned as-is
#   - CallToolResult  → returned as-is
#   - tuple[str, dict]  → (markdown text, structured_content dict)
#   - tuple[list[TextContent | ImageContent], dict]  → (content list,
#     structured_content dict) — E6 multimodal path
ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass
class ToolRegistration:
    """A single tool exposed via MCP."""
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    # Optional structured-content schema (MCP 2025-06-18+ feature).
    output_schema: dict[str, Any] | None = None


class ProtocolHandler:
    """
    Holds the catalogue of tools and wires them to an
    :class:`mcp.server.Server`.

    Usage::

        handler = ProtocolHandler()
        handler.register(
            name="query_knowledge_hub",
            description="Run a RAG query against a collection.",
            input_schema={...},
            handler=async_fn,
        )
        server = handler.build_server()
        # then run server over stdio / streamable-http / etc.
    """

    def __init__(
        self, *, server_name: str = "skdy-rag-server",
    ) -> None:
        self._server_name = server_name
        self._tools: dict[str, ToolRegistration] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register a tool. Re-registering with the same name overwrites."""
        if not name or not isinstance(name, str):
            raise ValueError(
                f"tool name must be a non-empty string, got {name!r}",
            )
        if not callable(handler):
            raise TypeError(f"handler for {name!r} must be callable")
        self._tools[name] = ToolRegistration(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            output_schema=output_schema,
        )
        logger.debug("registered tool: %s", name)

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if it existed."""
        return self._tools.pop(name, None) is not None

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def get(self, name: str) -> ToolRegistration | None:
        return self._tools.get(name)

    # ------------------------------------------------------------------
    # Dispatch (testable in isolation, no mcp Server needed)
    # ------------------------------------------------------------------
    async def dispatch(
        self, name: str, arguments: dict | None = None,
    ) -> list[TextContent | ImageContent] | tuple[
        list[TextContent | ImageContent], dict | None,
    ]:
        """
        Look up a tool by name, invoke its handler with ``arguments``,
        and normalise the return value into the shapes accepted by
        :func:`_to_call_tool_result`.

        ``ValueError`` is raised for unknown tools (the mcp library
        surfaces this as a protocol-level error). Handler exceptions
        are NOT caught here — see the module docstring's "Error
        mapping" section for why.
        """
        arguments = arguments or {}
        registration = self._tools.get(name)
        if registration is None:
            raise ValueError(f"Unknown tool: {name!r}")

        logger.info(
            "tool call: name=%s args_keys=%s",
            name, list(arguments.keys()),
        )
        result = await registration.handler(arguments)
        return _normalize_result(name, result)

    # ------------------------------------------------------------------
    # MCP server construction
    # ------------------------------------------------------------------
    def build_server(self) -> Server:
        """
        Build an :class:`mcp.server.Server` with all registered tools
        wired via the mcp v2 constructor API.

        The returned server is transport-agnostic; the caller chooses
        whether to run it on stdio or streamable-http.
        """
        handler = self  # for closure

        async def _on_list_tools(ctx, params) -> ListToolsResult:
            return ListToolsResult(tools=[
                Tool(
                    name=t.name,
                    description=t.description,
                    input_schema=t.input_schema,
                    output_schema=t.output_schema,
                )
                for t in handler._tools.values()
            ])

        async def _on_call_tool(
            ctx, params: CallToolRequestParams,
        ) -> CallToolResult:
            return await _to_call_tool_result(
                await handler.dispatch(params.name, params.arguments),
            )

        server: Server = Server(
            self._server_name,
            on_list_tools=_on_list_tools,
            on_call_tool=_on_call_tool,
        )
        return server


# ---------------------------------------------------------------------------
# Result normalisation
# ---------------------------------------------------------------------------

def tool_error(message: str) -> CallToolResult:
    """Build a ``CallToolResult`` signalling a known tool failure.

    Tools return this for *expected* parameter / business errors so the
    client receives ``CallToolResult(is_error=True)`` (per the MCP spec)
    instead of a protocol-level MCPError. See the module docstring's
    "Error mapping" section for the boundary.
    """
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        is_error=True,
    )


def _normalize_result(
    tool_name: str, result: Any,
) -> list[TextContent | ImageContent] | tuple[
    list[TextContent | ImageContent], dict | None,
] | CallToolResult:
    """
    Coerce a tool handler's return value into one of:

    - ``[TextContent | ImageContent, ...]`` — the content list for a
      ``CallToolResult``.
    - ``(content_list, structured_dict)`` — the same list paired
      with an MCP structured-content payload.
    - a :class:`CallToolResult` (returned unchanged, preserving its
      ``is_error`` flag — the error path for known tool failures).

    We do NOT fabricate ``TextContent._meta`` to smuggle structured
    content — that fails the mcp library's output validation when
    a tool declares an ``outputSchema``. Instead, the dispatcher
    passes the tuple through unchanged and
    :func:`_to_call_tool_result` builds the ``CallToolResult``
    with the structured content in its dedicated field.
    """
    # 1) CallToolResult → pass through unchanged (keeps is_error).
    if isinstance(result, CallToolResult):
        return result

    # 2) tuple → (unstructured, structured)
    if isinstance(result, tuple) and len(result) == 2:
        unstructured, structured = result
        if structured is not None and not isinstance(structured, dict):
            raise TypeError(
                f"tool {tool_name!r}: structured_content must be a "
                f"dict, got {type(structured).__name__}",
            )
        if isinstance(unstructured, str):
            return [TextContent(type="text", text=unstructured)], structured
        if isinstance(unstructured, list):
            return list(unstructured), structured
        raise TypeError(
            f"tool {tool_name!r}: tuple[0] must be str or list, "
            f"got {type(unstructured).__name__}",
        )

    # 3) list of content blocks
    if isinstance(result, list):
        return list(result)

    # 4) bare string
    if isinstance(result, str):
        return [TextContent(type="text", text=result)]

    raise TypeError(
        f"tool {tool_name!r}: unsupported return type "
        f"{type(result).__name__}; expected str, list[TextContent], "
        f"CallToolResult, or (unstructured, structured_dict)",
    )


async def _to_call_tool_result(
    normalised: list[TextContent | ImageContent] | tuple[
        list[TextContent | ImageContent], dict | None,
    ] | CallToolResult,
) -> CallToolResult:
    """
    Convert :func:`_normalize_result`'s output into the
    ``CallToolResult`` shape the mcp library expects.

    Accepts a bare content list, a ``(content, structured_dict)``
    tuple, or an already-built ``CallToolResult`` (passed through
    unchanged so its ``is_error`` flag survives). Empty structured
    dicts are dropped to avoid sending an empty ``structured_content``
    field — clients that compare against ``outputSchema`` would
    then complain that the field is present but empty.
    """
    if isinstance(normalised, CallToolResult):
        return normalised
    if isinstance(normalised, tuple):
        content, structured = normalised
    else:
        content, structured = normalised, None
    structured_arg = structured if structured else None
    return CallToolResult(
        content=list(content),
        structured_content=structured_arg,
    )


__all__ = [
    "ProtocolHandler",
    "ToolHandler",
    "ToolRegistration",
    "_normalize_result",
    "_to_call_tool_result",
    "tool_error",
]
