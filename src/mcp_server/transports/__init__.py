"""
MCP transports supported by this server (single source of truth).

Task 02.4 has to advertise the transports the server actually speaks.
Instead of hand-writing that list inside the capability document — where
it would drift away from ``--transport`` — the CLI argparse choices and
the capability document both read :data:`SUPPORTED_TRANSPORTS`.

Both transports are driven by the *same* :class:`mcp.server.Server`
instance built by
:meth:`~src.mcp_server.protocol_handler.ProtocolHandler.build_server`,
so the tool catalogue and the capability Resource cannot differ between
them (see ``streamable_http.py`` for the HTTP transport).
"""

from __future__ import annotations

#: Transports accepted by ``python -m main --transport``.
SUPPORTED_TRANSPORTS: tuple[str, ...] = ("stdio", "streamable-http")

#: Transport used when ``--transport`` is omitted.
DEFAULT_TRANSPORT: str = SUPPORTED_TRANSPORTS[0]

__all__ = ["DEFAULT_TRANSPORT", "SUPPORTED_TRANSPORTS"]
