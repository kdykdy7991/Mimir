"""Unified three-mode search application service (Task 04)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from src.application.contracts import (
    EvidenceScores,
    EvidenceV1,
    SearchDiagnostics,
    SearchRequest,
    SearchResult,
    SourceLocator,
    WarningCode,
    WarningV1,
    rank_evidence,
    reciprocal_rank_fusion,
)
from src.application.identifiers import collection_uuid, document_uuid
from src.application.services.search_filters import GovernanceFilterResolver


_SCORE_STAGE = {"dense": "dense", "sparse": "sparse", "fusion": "fusion", "rerank": "rerank"}


class UnifiedSearchService:
    """One orchestration path for all new and compatibility search tools."""

    def __init__(
        self, query_service: Any, governance: GovernanceFilterResolver,
        *, rerank_stage: Any | None = None,
    ) -> None:
        self._query = query_service
        self._governance = governance
        self._rerank = rerank_stage

    def search(self, request: SearchRequest) -> SearchResult:
        if len(request.queries) > 1:
            return self._search_multiple_queries(request)
        return self._search_one(request)

    def _search_one(self, request: SearchRequest) -> SearchResult:
        governed = self._governance.resolve(request.collection, request.filters)
        if governed.source_paths == frozenset():
            return SearchResult(
                query=request.query, collection=request.collection,
                collections=request.collections, mode=request.mode,
            )
        raw = self._query.search(
            request.query, top_k=request.top_k,
            mode=request.mode.value, filters=governed.metadata_filter(),
            collection=request.collection,
        )
        candidates = [item for item in raw.chunks if governed.matches(item)]
        warnings: list[WarningV1] = [
            WarningV1(
                code=WarningCode.PARTIAL_COLLECTION_FAILURE,
                message=str(reason),
            )
            for reason in (getattr(raw, "degraded_reasons", None) or [])
        ]
        if request.rerank and self._rerank is None:
            warnings.append(WarningV1(
                code=WarningCode.RERANK_DEGRADED,
                message="reranker unavailable; retrieval degraded",
            ))
        elif request.rerank and candidates:
            output = self._rerank.rerank(request.query, candidates)
            candidates = list(output.results)
            if getattr(output, "fallback", False):
                warnings.append(WarningV1(
                    code=WarningCode.RERANK_DEGRADED,
                    message="reranker unavailable; preserved retrieval order",
                ))
        evidence = [self._to_evidence(item, request) for item in candidates]
        ranked = rank_evidence(
            evidence, threshold=request.threshold, limit=request.top_k,
        )
        return SearchResult(
            query=request.query, collection=request.collection,
            collections=request.collections, mode=request.mode,
            evidence=ranked, warnings=tuple(warnings),
            diagnostics=SearchDiagnostics(
                trace_id=getattr(raw, "trace_id", None),
                degraded=bool(getattr(raw, "degraded", False)) or bool(warnings),
                dense_candidates=getattr(raw, "dense_count", None),
                sparse_candidates=getattr(raw, "sparse_count", None),
                fused_candidates=getattr(raw, "fused_count", None),
                executed_queries=request.queries,
                successful_collections=request.collections,
            ),
        )

    def _search_multiple_queries(self, request: SearchRequest) -> SearchResult:
        """Retrieve every caller-supplied query and fuse deterministically."""
        rankings: list[tuple[EvidenceV1, ...]] = []
        warnings: list[WarningV1] = []
        dense_count = sparse_count = fused_count = 0
        trace_ids: list[str] = []
        governed = self._governance.resolve(request.collection, request.filters)
        if governed.source_paths == frozenset():
            return SearchResult(
                query=request.query, collection=request.collection,
                collections=request.collections, mode=request.mode,
                diagnostics=SearchDiagnostics(
                    executed_queries=request.queries,
                    successful_collections=request.collections,
                ),
            )
        raw_by_key: dict[tuple[str, str, str], Any] = {}
        matches_by_key: dict[tuple[str, str, str], set[str]] = {}
        for query in request.queries:
            raw = self._query.search(
                query, top_k=request.top_k, mode=request.mode.value,
                filters=governed.metadata_filter(), collection=request.collection,
            )
            rows: list[EvidenceV1] = []
            for item in raw.chunks:
                if not governed.matches(item):
                    continue
                evidence = self._to_evidence(item, request, query=query)
                rows.append(evidence)
                key = (evidence.collection_id, evidence.document_id, evidence.chunk_id)
                raw_by_key.setdefault(key, item)
                matches_by_key.setdefault(key, set()).add(query)
            rankings.append(tuple(rows))
            dense_count += int(getattr(raw, "dense_count", 0) or 0)
            sparse_count += int(getattr(raw, "sparse_count", 0) or 0)
            fused_count += int(getattr(raw, "fused_count", 0) or 0)
            if getattr(raw, "trace_id", None):
                trace_ids.append(str(raw.trace_id))
            warnings.extend(
                WarningV1(
                    code=WarningCode.PARTIAL_COLLECTION_FAILURE,
                    message=str(reason), detail={"query": query},
                )
                for reason in (getattr(raw, "degraded_reasons", None) or [])
            )

        fused = reciprocal_rank_fusion(rankings, query_order=request.queries)
        if request.rerank and self._rerank is None:
            warnings.append(WarningV1(
                code=WarningCode.RERANK_DEGRADED,
                message="reranker unavailable; retrieval degraded",
            ))
        elif request.rerank and fused:
            fused_by_key = {
                (item.collection_id, item.document_id, item.chunk_id): item
                for item in fused
            }
            ordered_raw = [
                raw_by_key[(item.collection_id, item.document_id, item.chunk_id)]
                for item in fused
            ]
            output = self._rerank.rerank(request.query, ordered_raw)
            reranked: list[EvidenceV1] = []
            for item in output.results:
                mapped = self._to_evidence(item, request, query=request.query)
                key = (mapped.collection_id, mapped.document_id, mapped.chunk_id)
                matched = matches_by_key.get(key, {request.query})
                evidence = fused_by_key.get(key, mapped)
                scores = evidence.scores
                if item.source == "rerank":
                    scores = replace(scores, rerank=float(item.score))
                reranked.append(replace(
                    evidence, scores=scores,
                    matched_queries=tuple(q for q in request.queries if q in matched),
                ))
            fused = tuple(reranked)
            if getattr(output, "fallback", False):
                warnings.append(WarningV1(
                    code=WarningCode.RERANK_DEGRADED,
                    message="reranker unavailable; preserved fusion order",
                ))
        ranked = rank_evidence(
            fused, threshold=request.threshold, limit=request.top_k,
        )
        return SearchResult(
            query=request.query, collection=request.collection,
            collections=request.collections, mode=request.mode,
            evidence=ranked, warnings=tuple(warnings),
            diagnostics=SearchDiagnostics(
                trace_id=",".join(trace_ids) or None,
                degraded=bool(warnings), dense_candidates=dense_count,
                sparse_candidates=sparse_count, fused_candidates=fused_count,
                executed_queries=request.queries,
                successful_collections=request.collections,
            ),
        )

    @staticmethod
    def _to_evidence(
        item: Any, request: SearchRequest, *, query: str | None = None,
    ) -> EvidenceV1:
        metadata = item.metadata or {}
        stage = _SCORE_STAGE.get(str(item.source or ""))
        scores = EvidenceScores(**({stage: float(item.score)} if stage else {}))
        page = metadata.get("page_num", metadata.get("page"))
        try:
            page = int(page) if page is not None and int(page) >= 1 else None
        except (TypeError, ValueError):
            page = None
        heading = str(metadata.get("heading") or metadata.get("section") or "") or None
        source_path = str(metadata.get("source_path") or "")
        assets = metadata.get("asset_ids") or metadata.get("image_ids") or None
        if isinstance(assets, str):
            assets = tuple(x.strip() for x in assets.split(",") if x.strip()) or None
        content = str(item.text or "") if request.include_content else None
        return EvidenceV1(
            collection_id=str(collection_uuid(request.collection)),
            document_id=str(document_uuid(request.collection, source_path)),
            chunk_id=str(item.chunk_id),
            content_type=str(metadata.get("content_type") or "text"),
            source_locator=SourceLocator(
                kind="page" if page is not None else ("section" if heading else "chunk"),
                page=page, heading=heading,
            ),
            scores=scores, matched_queries=(query or request.query,),
            document_version=(str(metadata["document_version"]) if metadata.get("document_version") else None),
            chunk_version=(str(metadata["chunk_version"]) if metadata.get("chunk_version") else None),
            parent_chunk_id=(
                str(metadata.get("parent_chunk_id") or metadata.get("parent_id"))
                if metadata.get("parent_chunk_id") or metadata.get("parent_id") else None
            ),
            title=str(metadata.get("title") or "") or None,
            source_name=Path(source_path).name or None,
            content=content,
            content_preview=(content[:200] if content else None),
            heading_path=tuple(str(x) for x in metadata.get("heading_path") or ()) or None,
            asset_ids=tuple(str(x) for x in assets) if assets else None,
        )


__all__ = ["UnifiedSearchService"]
