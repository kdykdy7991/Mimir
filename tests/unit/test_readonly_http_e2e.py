"""
End-to-end collection isolation over the HTTP backend (review-fix #1/#2).

Spins up the real main-service app on an ephemeral port and drives it with
a real ``HttpRagReadOnlyClient`` over the wire, proving that a limited
principal's scope is honoured end-to-end:

  MCP principal → HttpRagReadOnlyClient forwards its scope header →
  internal API authenticates + builds a scoped principal → the SAME
  in-process read client's authorisation (filter/require) filters results.

No trust-all and no TrustedLocalPrincipal leakage over the wire.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass

import pytest

from src.mcp_server.auth.context import TrustedLocalPrincipal
from src.mcp_server.clients.errors import AccessDeniedError
from src.mcp_server.clients.http_client import HttpRagReadOnlyClient
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient

KEY = "e2e-key"


@dataclass(frozen=True)
class ScopedPrincipal:
    key_id: str
    name: str
    allowed_collections: frozenset


def _build_service(tmp_path):
    """A real in-process client wired to controlled storage data."""
    svc = InProcessRagReadOnlyClient(data_dir=str(tmp_path), config_path=None)
    svc._list_bm25 = lambda: {
        "finance": {"data_dir": str(tmp_path), "bm25_chunks": 5},
        "hr": {"data_dir": str(tmp_path), "bm25_chunks": 9},
    }
    svc._vector_counts = lambda settings, names: {}
    svc._document_counts = lambda names: {}
    svc._resolve_doc = lambda doc_id: {
        "fin-doc": ("finance", "/fin.pdf"),
        "hr-doc": ("hr", "/hr.pdf"),
    }.get(doc_id)
    svc._read_document_chunks = lambda collection, source: [
        {"id": "c", "text": "x", "metadata": {"title": "Doc"}},
    ]
    return svc


def _serve(app):
    """Run an ASGI app on an ephemeral port and return (base_url, server)."""
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
def chain(tmp_path, monkeypatch):
    import src.web_api.internal_mcp as im
    from src.web_api.app import create_app

    svc = _build_service(tmp_path)
    monkeypatch.setattr(im, "_client", lambda request: svc)
    monkeypatch.setattr(im, "_internal_key", lambda: KEY)
    app = create_app()
    base, server = _serve(app)
    try:
        yield HttpRagReadOnlyClient(
            base_url=base, api_key=KEY, timeout_s=10.0, trust_env=False,
        )
    finally:
        server.should_exit = True


def _finance():
    return ScopedPrincipal("k", "n", frozenset({"finance"}))


def _hr():
    return ScopedPrincipal("k", "n", frozenset({"hr"}))


def test_limited_principal_sees_only_its_collections(chain):
    finance = chain.list_collections(_finance())
    hr = chain.list_collections(_hr())
    assert {c.name for c in finance} == {"finance"}
    assert {c.name for c in hr} == {"hr"}


def test_doc_in_unauthorized_collection_is_denied(chain):
    with pytest.raises(AccessDeniedError):
        chain.get_document("fin-doc", _hr())
    info = chain.get_document("fin-doc", _finance())
    assert info.document_id == "fin-doc"


def test_trusted_local_cannot_leak_over_http(chain):
    # TrustedLocal sends no scope header → the internal API denies all.
    with pytest.raises(AccessDeniedError):
        chain.get_document("fin-doc", TrustedLocalPrincipal())
    assert chain.list_collections(TrustedLocalPrincipal()) == []


def test_query_with_unauthorized_collection_denied(chain):
    from src.mcp_server.clients.models import QueryRequest

    with pytest.raises(AccessDeniedError):
        chain.query_knowledge(
            QueryRequest(query="q", collection="finance"), _hr(),
        )