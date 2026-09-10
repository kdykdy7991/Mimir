"""
Review-fix #4 — ``run_server(--config=...)`` builds the configured backend
and fails fast, and the tools honour it (no stale ./config fallback).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp_server import server
from src.mcp_server.clients.http_client import HttpRagReadOnlyClient
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
from src.mcp_server.tools import common


def _write_cfg(tmp_path: Path, backend: str, base_url: str = "") -> str:
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        "mcp_server:\n"
        f"  rag_client_backend: {backend}\n"
        f"  rag_api_base_url: \"{base_url}\"\n"
        "  request_timeout_seconds: 30\n",
        encoding="utf-8",
    )
    return str(cfg)


def test_bootstrap_in_process_default(tmp_path):
    common.reset_client_cache()
    server._bootstrap_rag_client(_write_cfg(tmp_path, "in_process"))
    try:
        assert isinstance(common.client_for(), InProcessRagReadOnlyClient)
    finally:
        common.reset_client_cache()


def test_bootstrap_http_backend_from_config(tmp_path):
    common.reset_client_cache()
    server._bootstrap_rag_client(_write_cfg(tmp_path, "http", "http://api:8000"))
    try:
        client = common.client_for()
        assert isinstance(client, HttpRagReadOnlyClient)
        assert client._base_url == "http://api:8000"
    finally:
        common.reset_client_cache()


def test_bootstrap_fails_fast_on_invalid_backend(tmp_path):
    common.reset_client_cache()
    with pytest.raises(ValueError, match="rag_client_backend"):
        server._bootstrap_rag_client(_write_cfg(tmp_path, "banana"))
    common.reset_client_cache()


def test_http_missing_base_url_fails_fast_at_startup(tmp_path):
    common.reset_client_cache()
    with pytest.raises(ValueError, match="rag_api_base_url"):
        server._bootstrap_rag_client(_write_cfg(tmp_path, "http", ""))
    common.reset_client_cache()


def test_missing_config_file_tolerated(tmp_path):
    common.reset_client_cache()
    # Missing file → tolerant fallback to a usable in_process default.
    server._bootstrap_rag_client(str(tmp_path / "does-not-exist.yaml"))
    try:
        assert isinstance(common.client_for(), InProcessRagReadOnlyClient)
    finally:
        common.reset_client_cache()