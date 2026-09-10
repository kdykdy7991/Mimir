"""
MCP Server entry point (E1).

Two transports are supported, selected by ``--transport``:

- ``stdio`` (default) — speaks MCP/JSON-RPC over stdin/stdout.
  The ``stdout`` stream is reserved exclusively for MCP messages
  (JSON-RPC frames written by the ``mcp`` library); ``stderr``
  carries all server-side logs, trace events, and diagnostics.
  Mixing them corrupts the protocol stream and breaks every MCP
  client, so this module is strict about it when in stdio mode:

  - We attach a :class:`logging.StreamHandler` to stderr at startup.
  - We never call ``print(..., file=sys.stdout)`` anywhere in the
    process while the server is running.
  - All tool implementations must use the logger, not ``print()``.

- ``streamable-http`` — exposes the same ``Server`` over HTTP using
  the MCP spec's Streamable-HTTP transport (2025-03-26). Listens
  on ``--host``/``--port`` and serves the MCP endpoint at
  ``--mcp-path`` (default ``/mcp``). Logs go to stderr; access logs
  are disabled by default to keep the noise down.

  This mode is for remote / containerised / multi-tenant clients
  that can't (or don't want to) spawn the server as a subprocess.
  The stdio mode remains the primary path for local desktop MCP
  clients (Copilot, Claude Desktop, Cursor).

The server runs until the client closes the connection (stdio) or
until the process is signalled (HTTP).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Sequence

# Ensure project root is importable when launched as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.stdio import stdio_server  # noqa: E402

from src.core.settings import Settings, load_settings  # noqa: E402
from src.mcp_server.protocol_handler import ProtocolHandler  # noqa: E402


logger = logging.getLogger("mcp_server")


def _configure_logging(level: str = "INFO") -> None:
    """
    Route every log record to **stderr** (not stdout). The level is
    configurable from ``--log-level`` for debugging without breaking
    the MCP stream.
    """
    root = logging.getLogger()
    # Clear any handlers a previous test may have attached.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
    ))
    root.addHandler(handler)
    try:
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
    except Exception:  # noqa: BLE001
        root.setLevel(logging.INFO)
    # The mcp library is chatty at INFO; keep it at WARNING by default.
    logging.getLogger("mcp").setLevel(logging.WARNING)


def _register_default_tools(handler: ProtocolHandler) -> None:
    """
    Wire the project's tools into the registry.

    Imported lazily so a tool that fails to import (e.g. a missing
    optional dependency) does not prevent the rest of the server
    from starting. Failures are logged and the tool is skipped.
    """
    from src.mcp_server.tools.query_knowledge_hub import (
        register as register_query,
    )
    from src.mcp_server.tools.list_collections import (
        register as register_list,
    )
    from src.mcp_server.tools.get_document import (
        register as register_document,
    )
    from src.mcp_server.tools.get_document_summary import (
        register as register_summary,
    )
    from src.mcp_server.tools.get_document_chunks import (
        register as register_chunks,
    )

    for register_fn in (
        register_query, register_list, register_document,
        register_summary, register_chunks,
    ):
        try:
            register_fn(handler)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "failed to register tool %s: %s",
                getattr(register_fn, "__name__", "?"), exc,
            )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="skdy-rag-server",
        description=(
            "MCP server for the SKDY RAG stack. Speaks MCP/JSON-RPC "
            "over stdio or streamable-http. Logs go to stderr."
        ),
    )
    parser.add_argument(
        "--config", default="./config/settings.yaml",
        help="Path to settings.yaml (default: ./config/settings.yaml).",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="Python logging level (default: INFO).",
    )
    parser.add_argument(
        "--transport", default="stdio",
        choices=("stdio", "streamable-http"),
        help=(
            "MCP transport to use. 'stdio' (default) speaks JSON-RPC "
            "over stdin/stdout — the path every desktop MCP client "
            "(Copilot, Claude Desktop, Cursor) launches. "
            "'streamable-http' serves MCP/JSON-RPC over HTTP at "
            "--host/--port/--mcp-path for remote/containerised clients."
        ),
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Bind host for --transport streamable-http "
             "(default: 127.0.0.1; use 0.0.0.0 to accept external "
             "connections — only do this behind a reverse proxy).",
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Bind port for --transport streamable-http "
             "(default: 8765).",
    )
    parser.add_argument(
        "--mcp-path", default="/mcp",
        help="URL path that serves the MCP endpoint "
             "(default: /mcp).",
    )
    return parser.parse_args(argv)


async def run_server(
    config_path: str = "./config/settings.yaml",
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    mcp_path: str = "/mcp",
) -> None:
    """
    Build the protocol handler, register tools, and run over the
    requested transport.

    Dispatches to :func:`_run_stdio` or :func:`_run_streamable_http`
    based on ``transport``. Both paths share the same handler /
    server instance so the tool catalogue cannot drift between
    transports.
    """
    settings = Settings()

    # Load settings so tools can read them. We don't fail if the
    # file is missing — defaults are usable, and a malformed file
    # is logged and ignored (so the server still starts).
    try:
        settings = load_settings(config_path)
        logger.info(
            "loaded settings: llm=%s embedding=%s vector_store=%s",
            settings.llm.provider,
            settings.embedding.provider,
            settings.vector_store.backend,
        )
    except FileNotFoundError:
        logger.warning(
            "settings file %s not found — using defaults", config_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "failed to load settings from %s: %s — using defaults",
            config_path, exc,
        )

    handler = ProtocolHandler(
        server_name=settings.mcp.server_name,
        server_title=settings.mcp.server_title,
        server_description=settings.mcp.server_description,
        instructions=settings.mcp.instructions,
    )
    _register_default_tools(handler)
    logger.info(
        "registered tools: %s", ", ".join(handler.list_names()) or "(none)",
    )

    # Phase 4 §P4.3: build the RAG client from the *same* --config so the
    # configured backend (in_process | http) drives the tools and invalid /
    # http-without-base-url configs fail fast AT STARTUP, not on first call.
    # A missing settings file keeps the old missing-file tolerance (defaults).
    _bootstrap_rag_client(config_path)

    server = handler.build_server()

    if transport == "stdio":
        await _run_stdio(server)
    elif transport == "streamable-http":
        await _run_streamable_http(
            server, host=host, port=port, mcp_path=mcp_path,
            config_path=config_path,
        )
    else:
        raise ValueError(f"unknown transport: {transport!r}")


def _bootstrap_rag_client(config_path: str) -> None:
    """Build the configured RAG client and set it as the default.

    Uses the same ``--config`` that drives tool registration so backend
    selection is honored and invalid / http-without-base-url configs fail
    fast at startup. A missing file is tolerated (old behavior): fall back
    to a default in-process client.
    """
    from src.mcp_server.clients.factory import build_readonly_client
    from src.mcp_server.tools.common import DEFAULT_DATA, set_default_client

    try:
        client = build_readonly_client(config_path=config_path, data_dir=DEFAULT_DATA)
    except FileNotFoundError:
        logger.warning(
            "settings file %s not found — using in_process default client",
            config_path,
        )
        client = build_readonly_client(config_path=None, data_dir=DEFAULT_DATA)
    set_default_client(client)
    logger.info("rag client backend ready: %s", type(client).__name__)


async def _run_stdio(server) -> None:
    """Run the MCP server over stdio (default transport)."""
    async with stdio_server() as (read_stream, write_stream):
        logger.info("MCP server ready on stdio")
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


async def _run_streamable_http(
    server, *, host: str, port: int, mcp_path: str,
    config_path: str = "./config/settings.yaml",
) -> None:
    """Run the MCP server over streamable-HTTP via uvicorn.

    Wraps the synchronous uvicorn ``Server.run`` in ``to_thread``
    so we stay in the same async context as ``run_server`` —
    that way KeyboardInterrupt and other asyncio-aware signals
    behave the same as the stdio path.

    When ``mcp_access.enabled`` is true, the endpoint is protected by
    Bearer API-key authentication; the key database defaults to
    ``./data/db/mcp_access.db`` (PRD §8).
    """
    # Import locally so the stdio path doesn't pay the cost of
    # loading starlette/uvicorn on every startup.
    from src.mcp_server.auth.key_service import ApiKeyService
    from src.mcp_server.transports.streamable_http import (
        build_asgi_app,
        run_with_uvicorn,
    )

    # Do not turn an unreadable/malformed configuration into an open remote
    # endpoint. The initial settings load in ``run_server`` is intentionally
    # best-effort for the legacy stdio path; HTTP access control is a security
    # boundary and must fail closed instead.
    settings = load_settings(config_path)
    key_service: ApiKeyService | None = None
    if settings.mcp_access.enabled:
        key_service = ApiKeyService(db_path=settings.mcp_access.database_path)
    elif not _is_loopback_host(host):
        raise RuntimeError(
            "refusing to bind unauthenticated streamable-http MCP to a "
            "non-loopback host; enable mcp_access instead",
        )

    app = build_asgi_app(server, mcp_path=mcp_path, key_service=key_service)
    if key_service is not None:
        logger.info("mcp_access: Bearer API-key authentication enabled")
    logger.info(
        "starting streamable-http server on http://%s:%d%s",
        host, port, "/" + mcp_path.strip("/"),
    )
    # uvicorn.Server.run is blocking; offload to a worker thread
    # so this async coroutine returns cleanly on shutdown.
    await asyncio.to_thread(
        run_with_uvicorn, app, host=host, port=port, log_level="warning",
    )


def _is_loopback_host(host: str) -> bool:
    """Return whether an HTTP bind address is limited to the local machine."""
    return host.strip().lower() in {"127.0.0.1", "::1", "localhost"}


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging(args.log_level)
    try:
        asyncio.run(run_server(
            args.config,
            transport=args.transport,
            host=args.host,
            port=args.port,
            mcp_path=args.mcp_path,
        ))
    except KeyboardInterrupt:
        logger.info("interrupted, exiting")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("server crashed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
