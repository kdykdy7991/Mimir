"""
Phase 4 §P4.3 — explicit client backend selection (no silent fallback).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp_server.clients.factory import build_readonly_client
from src.mcp_server.clients.http_client import HttpRagReadOnlyClient
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient


def _write_cfg(tmp_path, backend, base_url="") -> str:
    cfg = Path(tmp_path) / "settings.yaml"
    cfg.write_text(
        "mcp_server:\n"
        f"  rag_client_backend: {backend}\n"
        f"  rag_api_base_url: \"{base_url}\"\n"
        "  request_timeout_seconds: 30\n"
        "  api_key: \"s3cr3t\"\n",
        encoding="utf-8",
    )
    return str(cfg)


def test_default_backend_is_in_process():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "s.yaml"
        cfg.write_text("mcp_server:\n  rag_client_backend: in_process\n", encoding="utf-8")
        client = build_readonly_client(config_path=str(cfg), data_dir=d)
        assert isinstance(client, InProcessRagReadOnlyClient)


def test_http_backend_returns_http_client():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "s.yaml"
        cfg.write_text(
            "mcp_server:\n  rag_client_backend: http\n"
            "  rag_api_base_url: http://api:8000\n",
            encoding="utf-8",
        )
        client = build_readonly_client(config_path=str(cfg), data_dir=d)
        assert isinstance(client, HttpRagReadOnlyClient)


def test_http_backend_missing_base_url_fails_fast():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ValueError, match="rag_api_base_url"):
            build_readonly_client(config_path=_write_cfg(d, "http", ""), data_dir=d)


def test_invalid_backend_fails_fast():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ValueError, match="rag_client_backend"):
            build_readonly_client(config_path=_write_cfg(d, "banana"), data_dir=d)