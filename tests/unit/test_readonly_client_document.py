"""
P1.2c — ``InProcessRagReadOnlyClient.get_document`` resolve/auth/read.

Exercises the client's document-detail path with patched collaborators so
no real vector store / embedding is needed.
"""

from __future__ import annotations

from unittest.mock import patch

from src.mcp_server.auth.context import TrustedLocalPrincipal


def _principal(*allowed):
    class P:
        key_id = "k"
        name = "n"
        allowed_collections = frozenset(allowed)
    return P()


def _client(data_dir="/tmp/unused"):
    from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
    return InProcessRagReadOnlyClient(data_dir=data_dir)


def test_get_document_maps_metadata():
    from src.mcp_server.clients.errors import ResourceNotFoundError

    client = _client()
    hits = [
        {"id": "d_0000_h", "text": "body", "metadata": {
            "title": "My Doc", "summary": "sum", "tags": ["t"],
            "source_path": "/x/y.pdf", "doc_type": "pdf", "chunk_index": 0,
        }},
        {"id": "d_0001_h2", "text": "b2", "metadata": {
            "title": "My Doc", "source_path": "/x/y.pdf", "chunk_index": 1,
        }},
    ]
    with patch.object(client, "_resolve_doc", return_value=("kb", "/x/y.pdf")), \
         patch.object(client, "_read_document_chunks", return_value=hits):
        info = client.get_document("d-uuid", TrustedLocalPrincipal())
    assert info.collection == "kb"
    assert info.title == "My Doc"
    assert info.summary == "sum"
    assert info.tags == ["t"]
    assert info.source == "/x/y.pdf"
    assert info.document_type == "pdf"
    assert info.chunk_count == 2


def test_get_document_unresolved_raises() -> None:
    from src.mcp_server.clients.errors import ResourceNotFoundError
    import pytest

    client = _client()
    with patch.object(client, "_resolve_doc", return_value=None):
        with pytest.raises(ResourceNotFoundError):
            client.get_document("unknown", TrustedLocalPrincipal())


def test_get_document_forbidden_is_denied() -> None:
    from src.mcp_server.clients.errors import AccessDeniedError
    import pytest

    client = _client()
    with patch.object(client, "_resolve_doc", return_value=("secret", "/x.pdf")):
        with pytest.raises(AccessDeniedError):
            client.get_document("d-uuid", _principal("other"))


def test_get_document_empty_store_raises() -> None:
    from src.mcp_server.clients.errors import ResourceNotFoundError
    import pytest

    client = _client()
    with patch.object(client, "_resolve_doc", return_value=("kb", "/x.pdf")), \
         patch.object(client, "_read_document_chunks", return_value=[]):
        with pytest.raises(ResourceNotFoundError):
            client.get_document("d-uuid", TrustedLocalPrincipal())