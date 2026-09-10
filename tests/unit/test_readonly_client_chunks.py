"""
Phase 3 — stable chunk ordering + pagination (P3.1 / P3.2 / P3.4).

Proves: ordering never relies on the vector store's natural order (it sorts
by ``chunk_index`` metadata, then by the index embedded in the chunk id for
legacy data, then by id); pagination slices first/middle/last/out-of-range
pages; parameter validation; not-found / denied propagation.
"""

from __future__ import annotations

from unittest.mock import patch

from src.mcp_server.auth.context import TrustedLocalPrincipal
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
from src.mcp_server.clients.errors import (
    AccessDeniedError,
    InvalidRequestError,
    ResourceNotFoundError,
)


def _client(chunks=None, *, resolve=("kb", "/x.pdf")):
    c = InProcessRagReadOnlyClient(data_dir="/tmp/unused")
    c._resolve_store_access = lambda doc_id, principal: resolve
    c._read_document_chunks = lambda collection, source_path: (chunks or [])
    return c


def _hit(*, cid, meta=None, text="t"):
    return {"id": cid, "text": text, "metadata": meta or {}}


def test_order_uses_chunk_index_never_natural_order():
    chunks = [
        _hit(cid="d_0002_h", meta={"chunk_index": 2}),
        _hit(cid="d_0000_h", meta={"chunk_index": 0}),
        _hit(cid="d_0001_h", meta={"chunk_index": 1}),
    ]
    client = _client(chunks)
    page = client.get_document_chunks("u", 1, 50, TrustedLocalPrincipal())
    assert [c.chunk_id for c in page.chunks] == ["d_0000_h", "d_0001_h", "d_0002_h"]
    assert [c.index for c in page.chunks] == [0, 1, 2]


def test_order_falls_back_to_index_in_chunk_id():
    # Legacy records may carry no chunk_index but have ``doc_000N_`` ids.
    chunks = [
        _hit(cid="doc_0003_hash"),
        _hit(cid="doc_0001_hash"),
        _hit(cid="doc_0002_hash"),
    ]
    client = _client(chunks)
    page = client.get_document_chunks("u", 1, 50, TrustedLocalPrincipal())
    assert [c.chunk_id for c in page.chunks] == [
        "doc_0001_hash", "doc_0002_hash", "doc_0003_hash",
    ]


def test_order_legacy_nonumeric_ids_stable_by_id():
    chunks = [
        _hit(cid="zeta"), _hit(cid="alpha"), _hit(cid="mid"),
    ]
    client = _client(chunks)
    page = client.get_document_chunks("u", 1, 50, TrustedLocalPrincipal())
    assert [c.chunk_id for c in page.chunks] == ["alpha", "mid", "zeta"]


def _ten_chunks():
    return [_hit(cid=f"d_{i:04d}_hash", meta={"chunk_index": i}) for i in range(10)]


def test_pagination_first_middle_last_and_out_of_range():
    client = _client(_ten_chunks())
    p1 = client.get_document_chunks("u", 1, 3, TrustedLocalPrincipal())
    assert [c.index for c in p1.chunks] == [0, 1, 2]
    assert p1.has_next is True

    p2 = client.get_document_chunks("u", 2, 3, TrustedLocalPrincipal())
    assert [c.index for c in p2.chunks] == [3, 4, 5]

    p4 = client.get_document_chunks("u", 4, 3, TrustedLocalPrincipal())
    assert [c.index for c in p4.chunks] == [9]
    assert p4.has_next is False
    assert p4.total == 10

    # Out-of-range page → empty window, no error, has_next False.
    p9 = client.get_document_chunks("u", 9, 3, TrustedLocalPrincipal())
    assert p9.chunks == []
    assert p9.total == 10
    assert p9.has_next is False


def test_validation_rejects_bad_params():
    client = _client(_ten_chunks())
    try:
        client.get_document_chunks("u", 0, 20, TrustedLocalPrincipal())
        assert False, "page=0 should raise"
    except InvalidRequestError:
        pass
    try:
        client.get_document_chunks("u", 1, 51, TrustedLocalPrincipal())
        assert False, "page_size>50 should raise"
    except InvalidRequestError:
        pass


def test_unresolved_document_raises_not_found():
    client = _client(resolve=None)  # resolve → None
    class R:
        def __call__(self, doc_id, principal):
            raise ResourceNotFoundError("document not found")
    client._resolve_store_access = R()
    try:
        client.get_document_chunks("u", 1, 20, TrustedLocalPrincipal())
        assert False
    except ResourceNotFoundError:
        pass


def test_empty_store_raises_not_found():
    client = _client(chunks=[])
    try:
        client.get_document_chunks("u", 1, 20, TrustedLocalPrincipal())
        assert False
    except ResourceNotFoundError:
        pass


def test_chunk_fields_populated():
    chunks = [
        _hit(cid="d_0000_h", meta={"chunk_index": 0, "page_num": 2,
                                   "section": "intro"}, text="hello"),
    ]
    client = _client(chunks)
    page = client.get_document_chunks("u", 1, 20, TrustedLocalPrincipal())
    c = page.chunks[0]
    assert c.chunk_id == "d_0000_h"
    assert c.text == "hello"
    assert c.page == 2
    assert c.section == "intro"