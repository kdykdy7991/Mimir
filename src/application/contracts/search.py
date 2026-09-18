"""Transport-neutral unified search request/result contracts (Task 04)."""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, replace

from src.application.contracts.budget import ResponseBudget
from src.application.contracts.evidence import EvidenceV1
from src.application.contracts.filters import EvidenceFilterV1
from src.application.contracts.messaging import WarningV1
from src.application.contracts.serialization import ContractError


class SearchMode(str, enum.Enum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"

    @classmethod
    def coerce(cls, value: object) -> "SearchMode":
        try:
            return value if isinstance(value, cls) else cls(str(value))
        except ValueError as exc:
            raise ContractError("mode must be 'dense', 'sparse', or 'hybrid'") from exc


class FailurePolicy(str, enum.Enum):
    FAIL_FAST = "fail_fast"
    ALLOW_PARTIAL = "allow_partial"

    @classmethod
    def coerce(cls, value: object) -> "FailurePolicy":
        try:
            return value if isinstance(value, cls) else cls(str(value))
        except ValueError as exc:
            raise ContractError(
                "failure_policy must be 'fail_fast' or 'allow_partial'",
            ) from exc


def _normalized_strings(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise ContractError(f"{name} must be an array of strings")
    try:
        rows = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ContractError(f"{name} must be an array of strings") from exc
    output: list[str] = []
    for item in rows:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(f"{name} must contain non-empty strings")
        normalized = item.strip()
        if normalized not in output:
            output.append(normalized)
    return tuple(output)


@dataclass(frozen=True)
class SearchRequest:
    query: str
    collection: str | None = None
    alternate_queries: tuple[str, ...] = ()
    collection_ids: tuple[str, ...] = ()
    failure_policy: FailurePolicy = FailurePolicy.FAIL_FAST
    mode: SearchMode = SearchMode.HYBRID
    filters: EvidenceFilterV1 | None = None
    top_k: int = 10
    rerank: bool = True
    threshold: float | None = None
    include_content: bool = True
    response_budget: ResponseBudget = ResponseBudget()

    def __post_init__(self) -> None:
        self.response_budget.check_query_length(self.query)
        self.response_budget.check_top_k(self.top_k)
        object.__setattr__(self, "query", self.query.strip())
        collection = self.collection
        if collection is not None:
            if not isinstance(collection, str) or not collection.strip():
                raise ContractError("collection must be a non-empty string or null")
            collection = collection.strip()
        alternate = _normalized_strings(self.alternate_queries, "alternate_queries")
        alternate = tuple(item for item in alternate if item != self.query)
        if len(alternate) > self.response_budget.alternate_query_max_count:
            raise ContractError(
                "alternate_queries exceeds the "
                f"{self.response_budget.alternate_query_max_count}-item limit",
            )
        for item in alternate:
            self.response_budget.check_query_length(item)
        if sum(map(len, alternate)) > self.response_budget.alternate_query_total_max_chars:
            raise ContractError(
                "alternate_queries exceeds the total character limit",
            )
        collections = _normalized_strings(self.collection_ids, "collection_ids")
        if collection is not None and collections:
            raise ContractError("collection and collection_ids are mutually exclusive")
        if collection is None and not collections:
            raise ContractError("collection or collection_ids is required")
        selected_count = len(collections) or 1
        if selected_count > self.response_budget.collection_max_count:
            raise ContractError(
                "collection_ids exceeds the "
                f"{self.response_budget.collection_max_count}-item limit",
            )
        candidate_work = len((self.query, *alternate)) * selected_count * self.top_k
        if candidate_work > self.response_budget.search_candidate_work_max:
            raise ContractError(
                "query × collection × top_k exceeds the search work budget",
            )
        object.__setattr__(self, "collection", collection)
        object.__setattr__(self, "alternate_queries", alternate)
        object.__setattr__(self, "collection_ids", collections)
        object.__setattr__(self, "failure_policy", FailurePolicy.coerce(self.failure_policy))
        object.__setattr__(self, "mode", SearchMode.coerce(self.mode))
        if self.filters is not None:
            object.__setattr__(self, "filters", EvidenceFilterV1.from_mapping(self.filters))
        if not isinstance(self.rerank, bool) or not isinstance(self.include_content, bool):
            raise ContractError("rerank and include_content must be booleans")
        if self.threshold is not None:
            if isinstance(self.threshold, bool) or not isinstance(self.threshold, (int, float)):
                raise ContractError("threshold must be a finite number or null")
            value = float(self.threshold)
            if not math.isfinite(value):
                raise ContractError("threshold must be a finite number or null")
            object.__setattr__(self, "threshold", value)

    @property
    def queries(self) -> tuple[str, ...]:
        return (self.query, *self.alternate_queries)

    @property
    def collections(self) -> tuple[str, ...]:
        return self.collection_ids or ((self.collection,) if self.collection else ())


@dataclass(frozen=True)
class SearchDiagnostics:
    trace_id: str | None = None
    degraded: bool = False
    dense_candidates: int | None = None
    sparse_candidates: int | None = None
    fused_candidates: int | None = None
    executed_queries: tuple[str, ...] = ()
    successful_collections: tuple[str, ...] = ()
    failed_collections: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchResult:
    query: str
    collection: str | None
    mode: SearchMode
    collections: tuple[str, ...] = ()
    evidence: tuple[EvidenceV1, ...] = ()
    warnings: tuple[WarningV1, ...] = ()
    diagnostics: SearchDiagnostics = SearchDiagnostics()
    truncated: bool = False


def final_score(item: EvidenceV1) -> float | None:
    """Score of the latest stage that actually executed."""
    scores = item.scores
    for value in (scores.rerank, scores.fusion, scores.dense, scores.sparse):
        if value is not None:
            return value
    return None


def rank_evidence(
    evidence: tuple[EvidenceV1, ...] | list[EvidenceV1],
    *,
    threshold: float | None = None,
    limit: int | None = None,
) -> tuple[EvidenceV1, ...]:
    """Deduplicate and deterministically rank evidence.

    The identity is collection/document/chunk. For duplicate identities the
    highest final-stage score wins; ties use the stable identity tuple.
    """
    chosen: dict[tuple[str, str, str], EvidenceV1] = {}
    for item in evidence:
        key = (item.collection_id, item.document_id, item.chunk_id)
        current = chosen.get(key)
        item_score = final_score(item)
        current_score = final_score(current) if current is not None else None
        item_value = item_score if item_score is not None else float("-inf")
        current_value = (
            current_score if current_score is not None else float("-inf")
        )
        if current is None or item_value > current_value:
            chosen[key] = item
    rows = [
        item for item in chosen.values()
        if threshold is None
        or (final_score(item) is not None and final_score(item) >= threshold)
    ]
    rows.sort(key=lambda item: (
        -(final_score(item) if final_score(item) is not None else float("-inf")),
        item.collection_id, item.document_id, item.chunk_id,
    ))
    if limit is not None:
        rows = rows[:limit]
    return tuple(rows)


def reciprocal_rank_fusion(
    rankings: tuple[tuple[EvidenceV1, ...], ...] | list[tuple[EvidenceV1, ...]],
    *,
    query_order: tuple[str, ...],
    rank_constant: int = 60,
) -> tuple[EvidenceV1, ...]:
    """Fuse ranked lists by stable evidence identity using fixed-parameter RRF."""
    if rank_constant < 1:
        raise ContractError("rank_constant must be positive")
    accumulated: dict[tuple[str, str, str], tuple[EvidenceV1, float, set[str]]] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            key = (item.collection_id, item.document_id, item.chunk_id)
            current = accumulated.get(key)
            score = 1.0 / (rank_constant + rank)
            if current is None:
                accumulated[key] = (item, score, set(item.matched_queries))
            else:
                exemplar, total, matched = current
                matched.update(item.matched_queries)
                accumulated[key] = (exemplar, total + score, matched)
    fused = []
    for item, score, matched in accumulated.values():
        ordered = tuple(query for query in query_order if query in matched)
        fused.append(replace(
            item,
            scores=replace(item.scores, fusion=score),
            matched_queries=ordered,
        ))
    return rank_evidence(fused)


__all__ = [
    "FailurePolicy", "SearchDiagnostics", "SearchMode", "SearchRequest",
    "SearchResult", "final_score", "rank_evidence", "reciprocal_rank_fusion",
]
