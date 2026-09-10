"""
Regression for review-fix #3: the in-process client's service access.

``self._services`` (attribute) used to shadow a same-named method, so any
call through injected application services raised ``'NoneType' object is
not callable``. This locks the real injected-services path.
"""

from __future__ import annotations

from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient


class _Bundle:
    """ApplicationServices-shaped object with .document / .query services."""

    def __init__(self):
        self.document = object()
        self.query = object()


def test_injected_services_resolve_without_crash():
    bundle = _Bundle()
    client = InProcessRagReadOnlyClient(
        data_dir="/tmp/unused", config_path=None, services=bundle,
    )
    # The authorisation/readure paths that reach application services must
    # work with a real injected bundle (no method/attribute shadowing).
    assert client._services_resolved() is bundle
    assert client._document_service() is bundle.document
    assert client._query_service() is bundle.query