"""
P1.2b — ``query_knowledge_hub`` routed through the readonly client.

Covers: empty-query rejection, collection selection (single auto / multi
required), rerank forwarding, and thin-handler formatting from a client
``KnowledgeQueryResult``. Retrieval itself is unit-tested on the client.
"""

from __future__ import annotations

from src.mcp_server.clients.models import EvidenceItem, KnowledgeQueryResult
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import query_knowledge_hub as qkh


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _dispatch(handler, args):
    h = ProtocolHandler()
    qkh.register(h)
    return _run(h.dispatch("query_knowledge_hub", args))


def test_empty_query_rejected():
    result = _run(qkh._query_knowledge_hub({"query": "  "}))
    assert result.is_error
    assert "query" in result.content[0].text


def test_multi_collection_omitted_returns_tool_error():
    """A multi-collection credential omitting 'collection' → operable error."""
    class Multi:
        key_id = "k"
        name = "n"
        allowed_collections = frozenset({"a", "b"})

    class BoomClient:
        def query_knowledge(self, request, principal):
            raise AssertionError("must not reach retrieval when collection omitted")

    async def boom(args):
        raise AssertionError("must not run")

    h = ProtocolHandler()
    qkh.register(h)
    out = _run(h.dispatch(
        "query_knowledge_hub", {"query": "x", "_client": BoomClient()},
        principal=Multi(),
    ))
    assert out.is_error
    assert "collection" in out.content[0].text


def test_single_collection_credential_auto_selects():
    """A single-collection principal omitting 'collection' resolves to it."""
    class Single:
        key_id = "k"
        name = "n"
        allowed_collections = frozenset({"only"})

    class FakeClient:
        def __init__(self): self.req = None
        def query_knowledge(self, request, principal):
            self.req = request
            return KnowledgeQueryResult(query=request.query, collection=request.collection, count=0)

    fake = FakeClient()
    h = ProtocolHandler()
    qkh.register(h)
    result = _run(h.dispatch(
        "query_knowledge_hub", {"query": "x", "_client": fake},
        principal=Single(),
    ))
    assert fake.req.collection == "only"
    assert fake.req.rerank is True


def test_no_rerank_sets_rerank_false():
    class FakeClient:
        def __init__(self): self.req = None
        def query_knowledge(self, request, principal):
            self.req = request
            return KnowledgeQueryResult(query=request.query, collection=request.collection, count=0)
    fake = FakeClient()
    _run(qkh._query_knowledge_hub({"query": "x", "no_rerank": True, "_client": fake}))
    assert fake.req.rerank is False


def test_rerank_defaults_true_and_top_k():
    class FakeClient:
        def __init__(self): self.req = None
        def query_knowledge(self, request, principal):
            self.req = request
            return KnowledgeQueryResult(query=request.query, collection=request.collection, count=0)
    fake = FakeClient()
    _run(qkh._query_knowledge_hub({"query": "x", "_client": fake}))
    assert fake.req.rerank is True
    assert fake.req.top_k == 10


def test_formatting_renders_evidence():
    result = KnowledgeQueryResult(
        query="q", collection="kb", count=2,
        evidence=[
            EvidenceItem(rank=1, chunk_id="c1", document_id="d1",
                         source="a.pdf", page=3, score=0.9,
                         text="evidence one" + "x" * 250, source_type="fusion"),
            EvidenceItem(rank=2, chunk_id="c2", document_id="d1",
                         source="b.md", score=0.5, text="short"),
        ],
    )
    class FakeClient:
        def query_knowledge(self, request, principal):
            return result
    md, structured = _run(qkh._query_knowledge_hub({"query": "q", "_client": FakeClient()}))
    assert structured["n_results"] == 2
    assert "a.pdf" in md
    assert "## References" in md
    assert structured["citations"][0]["text_excerpt"].endswith("…")
    assert len(structured["citations"][0]["text_excerpt"]) <= 201
    assert structured["citations"][1]["text_excerpt"] == "short"


def test_register_adds_tool():
    h = ProtocolHandler()
    qkh.register(h)
    assert h.has("query_knowledge_hub")