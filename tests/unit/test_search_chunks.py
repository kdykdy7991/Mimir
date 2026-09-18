from __future__ import annotations

import asyncio

from src.application.contracts import (
    EvidenceScores,
    EvidenceV1,
    SearchDiagnostics,
    SearchMode,
    SearchResult,
    SourceLocator,
)
from src.mcp_server.tools.search_chunks import _search_chunks


class Client:
    def __init__(self):
        self.request = None

    def search(self, request, principal):
        self.request = request
        return SearchResult(
            query=request.query, collection=request.collection, mode=request.mode,
            evidence=(EvidenceV1(
                collection_id="cid", document_id="did", chunk_id="chunk",
                content_type="table", source_locator=SourceLocator(kind="page", page=2),
                scores=EvidenceScores(dense=.8), matched_queries=(request.query,),
                content="evidence body",
            ),), diagnostics=SearchDiagnostics(trace_id="trace"),
        )


def test_search_chunks_maps_safe_request_and_evidence():
    client = Client()
    _markdown, body = asyncio.run(_search_chunks({
        "query": "q", "collection": "kb", "mode": "dense",
        "filters": {"content_types": ["table"], "tag_operator": "and"},
        "rerank": False, "_client": client,
    }))
    assert client.request.mode is SearchMode.DENSE
    assert client.request.filters.content_types == ("table",)
    assert body["evidence"][0]["scores"]["dense"] == .8
    assert body["diagnostics"]["trace_id"] == "trace"


def test_search_chunks_maps_multi_query_and_collection_request():
    client = Client()
    _markdown, body = asyncio.run(_search_chunks({
        "query": "q", "alternate_queries": ["q2"],
        "collection_ids": ["a", "b"], "failure_policy": "allow_partial",
        "rerank": False, "_client": client,
    }))
    assert client.request.queries == ("q", "q2")
    assert client.request.collections == ("a", "b")
    assert client.request.failure_policy.value == "allow_partial"
    assert body["collections"] == []  # fake client leaves optional result field empty


def test_search_chunks_rejects_invalid_mode_and_filter():
    result = asyncio.run(_search_chunks({"query": "q", "mode": "bad"}))
    assert result.is_error
    result = asyncio.run(_search_chunks({
        "query": "q", "filters": {"include_descendants": True},
    }))
    assert result.is_error
