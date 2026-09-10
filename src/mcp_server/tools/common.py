"""
Shared helpers for the read tools (plan §7 ``tools/common.py``).

The tools never build an Embedding / VectorStore / SQLite stack
themselves; they obtain a :class:`RagReadOnlyClient` here. A
process-wide cache keeps one in-process client per
``(config_path, data_dir)`` so repeated calls are cheap and test
data-dirs stay isolated. ``set_default_client`` lets the server inject
an explicit client (the migration default is the in-process client).
"""

from __future__ import annotations

from typing import Any

from src.mcp_server.clients.factory import build_readonly_client

DEFAULT_CONFIG = "./config/settings.yaml"
DEFAULT_DATA = "./data"

_default_client: Any | None = None
_cache: dict[tuple[str, str], Any] = {}


def set_default_client(client: Any) -> None:
    """Inject an explicit client (used by ``server.py`` at boot)."""
    global _default_client
    _default_client = client


def reset_client_cache() -> None:
    """Drop all cached clients (test isolation)."""
    global _default_client
    _default_client = None
    _cache.clear()


def client_for(config_path: str | None = None, data_dir: str | None = None) -> Any:
    """Return the configured backend client, cached per key.

    The backend is chosen from config (``mcp_server.rag_client_backend``)
    via :func:`build_readonly_client` — explicit, no silent fallback.
    """
    if _default_client is not None:
        return _default_client
    key = (config_path or DEFAULT_CONFIG, data_dir or DEFAULT_DATA)
    client = _cache.get(key)
    if client is None:
        client = build_readonly_client(config_path=key[0], data_dir=key[1])
        _cache[key] = client
    return client


def client_from_args(args: dict[str, Any]) -> Any:
    """Resolve a client honouring an injected ``_client`` or the arg hints."""
    injected = args.get("_client")
    if injected is not None:
        return injected
    return client_for(
        args.get("_config_path"),
        args.get("_data_dir"),
    )


__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_DATA",
    "client_for",
    "client_from_args",
    "reset_client_cache",
    "set_default_client",
]