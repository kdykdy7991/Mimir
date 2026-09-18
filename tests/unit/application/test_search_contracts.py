from __future__ import annotations

from dataclasses import replace

import pytest

from src.application.contracts import (
    ContractError,
    EvidenceFilterV1,
    SearchMode,
    SearchRequest,
    EvidenceScores,
    EvidenceV1,
    SourceLocator,
    rank_evidence,
    reciprocal_rank_fusion,
)


def test_search_request_defaults_and_normalizes():
    request = SearchRequest(query="  hello  ", collection=" kb ")
    assert request.query == "hello"
    assert request.collection == "kb"
    assert request.mode is SearchMode.HYBRID
    assert request.top_k == 10


@pytest.mark.parametrize("mode", ["dense", "sparse", "hybrid"])
def test_search_modes(mode):
    assert SearchRequest(query="q", collection="kb", mode=mode).mode.value == mode


def test_invalid_mode_threshold_and_budget_rejected():
    with pytest.raises(ContractError):
        SearchRequest(query="q", collection="kb", mode="other")
    with pytest.raises(ContractError):
        SearchRequest(query="q", collection="kb", threshold=float("nan"))
    with pytest.raises(ContractError):
        SearchRequest(query="", collection="kb")


def test_filter_mapping_is_validated():
    request = SearchRequest(
        query="q", collection="kb",
        filters={"tag_ids": ["a", "b"], "tag_operator": "or"},
    )
    assert isinstance(request.filters, EvidenceFilterV1)
    assert request.filters.tag_operator.value == "or"


def _evidence(chunk_id, score):
    return EvidenceV1(
        collection_id="kb", document_id="doc", chunk_id=chunk_id,
        content_type="text", source_locator=SourceLocator(kind="chunk"),
        scores=EvidenceScores(fusion=score), matched_queries=("q",),
    )


def test_rank_evidence_deduplicates_thresholds_and_tie_breaks():
    rows = rank_evidence([
        _evidence("b", 0.5), _evidence("a", 0.5),
        _evidence("b", 0.8), _evidence("low", 0.1),
    ], threshold=0.5, limit=2)
    assert [(row.chunk_id, row.scores.fusion) for row in rows] == [
        ("b", 0.8), ("a", 0.5),
    ]


def test_multi_query_and_collection_inputs_normalize_without_changing_primary():
    request = SearchRequest(
        query=" q1 ", alternate_queries=(" q2 ", "q1", "q2", "q3"),
        collection_ids=(" kb-b ", "kb-a", "kb-b"),
        failure_policy="allow_partial",
    )
    assert request.queries == ("q1", "q2", "q3")
    assert request.collections == ("kb-b", "kb-a")
    assert request.failure_policy.value == "allow_partial"


def test_multi_search_rejects_conflicts_and_query_budget():
    with pytest.raises(ContractError, match="mutually exclusive"):
        SearchRequest(query="q", collection="kb", collection_ids=("kb2",))
    with pytest.raises(ContractError, match="3-item"):
        SearchRequest(
            query="q", collection="kb",
            alternate_queries=("a", "b", "c", "d"),
        )
    with pytest.raises(ContractError, match="search work budget"):
        SearchRequest(
            query="q", alternate_queries=("a", "b", "c"),
            collection_ids=tuple(f"kb-{i}" for i in range(6)), top_k=50,
        )


def test_rrf_is_deterministic_tracks_queries_and_keeps_collection_identity():
    first = (_evidence("same", .9), _evidence("b", .8))
    second = (
        replace(_evidence("same", .7), matched_queries=("q2",)),
        replace(
            _evidence("same", .6), collection_id="other",
            matched_queries=("q2",),
        ),
    )
    fused = reciprocal_rank_fusion(
        [first, second], query_order=("q", "q2"), rank_constant=60,
    )
    assert [(item.collection_id, item.chunk_id) for item in fused] == [
        ("kb", "same"), ("kb", "b"), ("other", "same"),
    ]
    assert fused[0].matched_queries == ("q", "q2")
    assert fused[0].scores.fusion == pytest.approx(2 / 61)
