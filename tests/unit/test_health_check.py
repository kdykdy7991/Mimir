"""
Review-fix #2 — the standalone MCP health gate uses the shared internal
credential and fails closed when it is absent.

Runs the REAL health_check.main against a live uvicorn-served internal API:
  - correct key injected (env MCP_INTERNAL_API_KEY)  -> healthy (exit 0)
  - missing key                                     -> unhealthy (exit 1)
  - wrong key                                       -> unhealthy (exit 1)
"""

from __future__ import annotations

import importlib.util
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "health_check", _ROOT / "deploy" / "mcp" / "health_check.py",
)
health_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(health_check)


KEY = "health-secret"


def _serve(app):
    import uvicorn

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port,
                         log_level="warning", access_log=False)
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server


@pytest.fixture()
def liven_url(monkeypatch):
    import src.web_api.internal_mcp as im
    from src.web_api.app import create_app

    class Fake:
        def list_collections(self, principal):
            return []

    monkeypatch.setattr(im, "_internal_key", lambda: KEY)
    monkeypatch.setattr(im, "_client", lambda request: Fake())
    app = create_app()
    url, server = _serve(app)
    try:
        yield url
    finally:
        server.should_exit = True


def test_health_ok_with_key(liven_url, monkeypatch):
    monkeypatch.setenv("MCP_INTERNAL_API_KEY", KEY)
    assert health_check.main(["--base-url", liven_url]) == 0


def test_health_fails_http_when_key_missing(liven_url, monkeypatch):
    monkeypatch.delenv("MCP_INTERNAL_API_KEY", raising=False)
    assert health_check.main(["--base-url", liven_url]) == 1


def test_health_fails_http_when_key_wrong(liven_url, monkeypatch):
    monkeypatch.setenv("MCP_INTERNAL_API_KEY", "wrong-secret")
    assert health_check.main(["--base-url", liven_url]) == 1