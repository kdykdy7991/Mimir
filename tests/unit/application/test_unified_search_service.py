from types import SimpleNamespace

from src.application.contracts import EvidenceFilterV1, SearchRequest
from src.application.services.search_filters import ResolvedGovernanceFilter
from src.application.services.search_service import UnifiedSearchService
from src.core.types import ChunkRecord, RetrievalResult


class Query:
    def __init__(self, chunks):
        self.chunks = chunks
        self.called = None

    def search(self, query, **kwargs):
        self.called = (query, kwargs)
        return SimpleNamespace(
            chunks=self.chunks, trace_id="trace", degraded=False,
            dense_count=2, sparse_count=1, fused_count=2,
        )


class Governance:
    def __init__(self, resolved):
        self.resolved = resolved

    def resolve(self, collection, filters):
        return self.resolved


def _hit(cid, score, source="fusion", path="/a.pdf", content_type="text"):
    return RetrievalResult(
        chunk=ChunkRecord(id=cid, text=f"body-{cid}", metadata={
            "source_path": path, "content_type": content_type, "page_num": 2,
        }), score=score, source=source,
    )


def test_unified_search_routes_mode_filters_threshold_and_maps_evidence():
    query = Query([_hit("low", .2), _hit("high", .9)])
    service = UnifiedSearchService(
        query, Governance(ResolvedGovernanceFilter(
            source_paths=frozenset({"/a.pdf"}),
        )),
    )
    result = service.search(SearchRequest(
        query="q", collection="kb", mode="hybrid", threshold=.5,
        filters=EvidenceFilterV1(file_types=["pdf"]), rerank=False,
    ))
    assert [e.chunk_id for e in result.evidence] == ["high"]
    assert result.evidence[0].scores.fusion == .9
    assert result.evidence[0].source_locator.page == 2
    assert query.called[1]["mode"] == "hybrid"
    assert query.called[1]["filters"] == {"source_path": "/a.pdf"}


def test_empty_governance_result_does_not_touch_retrieval():
    query = Query([])
    service = UnifiedSearchService(
        query, Governance(ResolvedGovernanceFilter(source_paths=frozenset())),
    )
    result = service.search(SearchRequest(query="q", collection="kb"))
    assert result.evidence == ()
    assert query.called is None


def test_content_and_source_filters_are_defensive():
    query = Query([
        _hit("dense-table", .8, "dense", content_type="table"),
        _hit("sparse-text", .9, "sparse", content_type="text"),
    ])
    service = UnifiedSearchService(
        query, Governance(ResolvedGovernanceFilter(
            content_types=frozenset({"table"}),
            source_types=frozenset({"dense"}),
        )),
    )
    result = service.search(SearchRequest(
        query="q", collection="kb", mode="dense", rerank=False,
    ))
    assert [e.chunk_id for e in result.evidence] == ["dense-table"]
    assert result.evidence[0].scores.dense == .8


def test_include_content_false_omits_body():
    service = UnifiedSearchService(
        Query([_hit("c", .7)]), Governance(ResolvedGovernanceFilter()),
    )
    result = service.search(SearchRequest(
        query="q", collection="kb", rerank=False, include_content=False,
    ))
    assert result.evidence[0].content is None
    assert result.evidence[0].content_preview is None


def test_multi_query_fuses_matches_and_reranks_once():
    class MultiQuery:
        def __init__(self):
            self.calls = []

        def search(self, query, **kwargs):
            self.calls.append(query)
            chunks = {
                "q1": [_hit("shared", .9), _hit("first", .8)],
                "q2": [_hit("shared", .7), _hit("second", .6)],
            }[query]
            return SimpleNamespace(
                chunks=chunks, trace_id=query, degraded=False,
                dense_count=0, sparse_count=0, fused_count=2,
            )

    class Rerank:
        def __init__(self):
            self.calls = []

        def rerank(self, query, rows):
            self.calls.append((query, [row.chunk_id for row in rows]))
            for index, row in enumerate(rows):
                row.source = "rerank"
                row.score = 1.0 - index / 10
            return SimpleNamespace(results=rows, fallback=False)

    query = MultiQuery()
    rerank = Rerank()
    result = UnifiedSearchService(
        query, Governance(ResolvedGovernanceFilter()), rerank_stage=rerank,
    ).search(SearchRequest(
        query="q1", alternate_queries=("q2",), collection="kb",
    ))

    assert query.calls == ["q1", "q2"]
    assert len(rerank.calls) == 1
    assert rerank.calls[0][1] == ["shared", "first", "second"]
    assert result.evidence[0].matched_queries == ("q1", "q2")
    assert result.evidence[0].scores.fusion is not None
    assert result.evidence[0].scores.rerank == 1.0
    assert result.diagnostics.executed_queries == ("q1", "q2")
