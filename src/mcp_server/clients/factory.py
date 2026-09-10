"""
Explicit read-only client factory (Phase 4 §P4.3).

Selects the ``RagReadOnlyClient`` backend from ``mcp_server`` config:

- ``in_process`` (default) — local application services, unchanged local
  behaviour;
- ``http`` — the main service's internal read-only HTTP API.

Selection is explicit: an unknown backend or an ``http`` backend without
a base URL is a hard error (fail-fast). There is NO silent fallback to
in-process.
"""

from __future__ import annotations

from pathlib import Path

from src.core.settings import Settings, load_settings
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
from src.mcp_server.clients.http_client import HttpRagReadOnlyClient


def build_readonly_client(
    config_path: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> "object":
    """Build the configured ``RagReadOnlyClient`` without silent fallback."""
    if config_path:
        settings = load_settings(str(config_path))
    else:
        p = Path("./config/settings.yaml")
        if p.is_file():
            settings = load_settings(str(p))
        else:
            settings = Settings()

    backend = settings.mcp_server.rag_client_backend
    if backend == "in_process":
        return InProcessRagReadOnlyClient(
            config_path=str(config_path) if config_path else None,
            data_dir=str(data_dir) if data_dir else None,
        )

    if backend == "http":
        base_url = (settings.mcp_server.rag_api_base_url or "").strip()
        if not base_url:
            raise ValueError(
                "mcp_server.rag_client_backend='http' requires "
                "mcp_server.rag_api_base_url (fail-fast; refusing to silent-"
                "fall back to in_process)",
            )
        return HttpRagReadOnlyClient(
            base_url=base_url,
            api_key=settings.mcp_server.api_key,
            timeout_s=settings.mcp_server.request_timeout_seconds,
        )

    raise ValueError(
        f"mcp_server.rag_client_backend must be 'in_process' or 'http', "
        f"got {backend!r}",
    )


__all__ = ["build_readonly_client"]