"""
Streamable-HTTP transport for the MCP server.

Exposes a :class:`mcp.server.Server` over HTTP using
:mod:`mcp.server.streamable_http_manager` so remote agents
(web apps, containerised clients, the OpenAI Agents SDK, ...)
can speak MCP/JSON-RPC without needing to spawn a stdio
subprocess.

Architecture
------------
The :class:`StreamableHTTPSessionManager` owns per-session
``StreamableHTTPServerTransport`` instances and routes ASGI
requests to them. It is lifecycle-managed via the Starlette
``lifespan`` hook so the underlying anyio task group starts
when the ASGI server boots and shuts down cleanly on exit.

Endpoint shape (per MCP spec 2025-03-26)::

    POST   {mcp_path}   → JSON-RPC request, response in body or SSE
    GET    {mcp_path}   → opens an SSE stream for server→client notifications
    DELETE {mcp_path}   → terminates a session

All paths return 405 for unsupported methods. Health probes
(``GET /health``) return 200 OK without touching MCP state.

Why a dedicated module?
-----------------------
``server.py`` already builds the ``Server`` via the protocol
handler — we want to reuse that exactly (same tool registry,
same dispatch path) so the two transports cannot drift apart.
This module takes an already-built ``Server`` and turns it
into an ASGI app; transport selection lives in
:mod:`src.mcp_server.server`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from mcp.server.streamable_http_manager import (
    StreamableHTTPASGIApp,
    StreamableHTTPSessionManager,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from mcp.server import Server

logger = logging.getLogger(__name__)


# HTTP status codes returned by the ASGI app.
_OK = 200


def build_asgi_app(
    server: "Server",
    *,
    mcp_path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
) -> Starlette:
    """
    Wrap ``server`` in a Starlette ASGI application.

    Args:
        server: An already-built :class:`mcp.server.Server`
            (typically via ``ProtocolHandler.build_server()``).
            The same instance is shared across sessions — the
            session manager internally creates per-session
            transports that delegate to ``server``.
        mcp_path: URL path that serves the MCP endpoint.
            Defaults to ``/mcp`` (the conventional location).
        json_response: When ``True``, the server returns plain
            JSON bodies for request/response exchanges. When
            ``False`` (default), it streams responses as SSE —
            required for clients that expect server-sent
            notifications during long-running tool calls.
        stateless: When ``True``, every HTTP request is
            independent (no session_id, no SSE stream). Useful
            for very simple request/response clients but loses
            the spec's resumability features. Default ``False``
            matches the stdio transport's session model.

    Returns:
        A Starlette ``ASGIApp`` ready to serve with uvicorn /
        hypercorn / any ASGI server.

    Note:
        The MCP endpoint is mounted via
        :class:`StreamableHTTPASGIApp` — the canonical adapter
        from the ``mcp`` library — so it speaks the full
        streamable-HTTP protocol (POST for requests, GET for the
        server→client SSE stream, DELETE for session teardown)
        without us hand-rolling any of it.
    """
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=json_response,
        stateless=stateless,
    )
    # StreamableHTTPASGIApp is a tiny ASGI wrapper around the
    # session manager's own asgi_app (which already includes
    # request-body-size limiting middleware). Starlette's Route
    # accepts an ASGI sub-app as its endpoint — when invoked,
    # it calls __call__(scope, receive, send) and does NOT
    # expect a Response return value (the sub-app sends the
    # response itself).
    mcp_asgi_app = StreamableHTTPASGIApp(manager)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with manager.run():
            logger.info(
                "streamable-http transport ready (path=%s stateless=%s)",
                mcp_path, stateless,
            )
            try:
                yield
            finally:
                logger.info("streamable-http transport shutting down")

    async def health(_request: Request) -> JSONResponse:
        """Lightweight liveness probe — never touches MCP state."""
        return JSONResponse(
            {"status": "ok", "transport": "streamable-http"},
            status_code=_OK,
        )

    # Normalise mcp_path: must start with "/", no duplicate slashes.
    norm_path = "/" + mcp_path.strip("/")

    app = Starlette(
        lifespan=lifespan,
        routes=[
            # The MCP endpoint accepts POST/GET/DELETE only — the
            # ASGI sub-app responds 405 (or appropriate error)
            # for other methods, but Starlette will route any
            # method to it because it's an ASGI sub-app rather
            # than a function endpoint. That's fine: the spec
            # requires the endpoint to handle its own 405s.
            Route(norm_path, endpoint=mcp_asgi_app),
            Route("/health", health, methods=["GET"]),
        ],
    )
    # Stash the manager so tests / advanced callers can introspect it.
    app.state.manager = manager  # type: ignore[attr-defined]
    return app


__all__ = ["build_asgi_app"]


# ---------------------------------------------------------------------------
# Optional helpers (used by ``server.py`` to actually run the ASGI app
# via uvicorn when the CLI selects ``--transport streamable-http``).
# ---------------------------------------------------------------------------

def run_with_uvicorn(
    app: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    log_level: str = "info",
) -> None:
    """
    Serve ``app`` on ``host:port`` via uvicorn.

    Blocks the calling thread. ``log_level`` is forwarded to
    uvicorn; pass ``"warning"`` (or higher) in production to keep
    the access log from drowning out application logs.
    """
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level,
        # stdout is reserved for ASGI access logs by default;
        # route them to stderr so we don't fight with anything
        # else in the process that might use stdout.
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()