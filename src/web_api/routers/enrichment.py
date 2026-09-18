"""Management API for reviewable tag enrichment suggestions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, Path as ApiPath, Query
from pydantic import BaseModel, Field

from src.application.composition import ApplicationServices
from src.application.contracts import to_jsonable
from src.application.services.tag_enrichment_service import TagEnrichmentService
from src.ingestion.storage import (
    DerivedContentStore, EnrichmentMetricsStore, EnrichmentStore, RevisionStore,
)
from src.web_api.dependencies import DEFAULT_DATA_DIR, get_application_services
from src.web_api.errors import BadRequestError, ChunkNotFoundError, ConflictError
from src.web_api.mappers import collection_uuid

router = APIRouter(tags=["knowledge-enrichment"])
PROMPT_VERSION = "tag-suggestions-v1"


class ReviewRequest(BaseModel):
    approve: bool
    resolved_tag_id: str | None = Field(None, min_length=1)


class DerivedEnabledRequest(BaseModel):
    enabled: bool


def _strict_generator(llm: Any, metrics: EnrichmentMetricsStore, model: str):
    def generate(text: str, vocabulary: tuple[str, ...]):
        prompt = (
            "Return JSON only: an array of objects with name (string) and "
            "confidence (0..1). Prefer names from vocabulary. Do not include "
            "explanations.\nVocabulary: " + json.dumps(vocabulary, ensure_ascii=False)
            + "\nDocument:\n" + text[:12_000]
        )
        started = time.perf_counter()
        raw = None
        try:
            raw = llm.chat([
                {"role": "system", "content": "You suggest concise document tags."},
                {"role": "user", "content": prompt},
            ], temperature=0)
            parsed = json.loads(str(raw).strip())
            if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
                raise ValueError("tag model output must be a JSON array")
            metrics.record(
                operation="tag_suggestions", model=model,
                prompt_version=PROMPT_VERSION,
                latency_ms=(time.perf_counter() - started) * 1000,
                input_chars=len(prompt), output_chars=len(str(raw)), success=True,
            )
            return parsed
        except Exception as exc:
            metrics.record(
                operation="tag_suggestions", model=model,
                prompt_version=PROMPT_VERSION,
                latency_ms=(time.perf_counter() - started) * 1000,
                input_chars=len(prompt),
                output_chars=(len(str(raw)) if raw is not None else None),
                success=False, error_type=type(exc).__name__,
            )
            raise
    return generate


def _store() -> EnrichmentStore:
    return EnrichmentStore(Path(DEFAULT_DATA_DIR) / "db" / "enrichment.db")


def _derived_store() -> DerivedContentStore:
    return DerivedContentStore(Path(DEFAULT_DATA_DIR) / "db" / "derived.db")


def _metrics_store() -> EnrichmentMetricsStore:
    return EnrichmentMetricsStore(Path(DEFAULT_DATA_DIR) / "db" / "enrichment_metrics.db")


def _service(services: ApplicationServices) -> TagEnrichmentService:
    llm = services.engines.llm
    model = str(getattr(llm, "model", type(llm).__name__ if llm else "unavailable"))
    generator = _strict_generator(llm, _metrics_store(), model) if llm is not None else lambda *_: ()
    return TagEnrichmentService(
        _store(), generator, model=model, prompt_version=PROMPT_VERSION,
        enabled=bool(services.engines.settings.tag_enrichment_enabled and llm is not None),
    )


@router.post("/documents/{document_id}/chunks/{chunk_id}/tag-suggestions")
def generate_tag_suggestions(
    document_id: str = ApiPath(...), chunk_id: str = ApiPath(...),
    services: ApplicationServices = Depends(get_application_services),
) -> dict[str, Any]:
    revisions = RevisionStore(Path(DEFAULT_DATA_DIR) / "db" / "revisions.db")
    # The active revision contains the authorized/current content selected by
    # the management plane; absent history is not silently fabricated here.
    revision = revisions.active_for_document_chunk(document_id, chunk_id)
    if revision is None:
        raise ChunkNotFoundError("chunk revision not found or not accessible")
    cid = str(collection_uuid(revision.collection))
    vocabulary = [
        (row["tag_id"], row["name"]) for row in services.db.list_tags(cid)
    ]
    rows = _service(services).suggest(
        collection=revision.collection, document_id=document_id,
        revision_id=revision.revision_id, text=revision.text,
        vocabulary=vocabulary,
        manual_tag_ids=services.db.document_tag_ids(document_id),
    )
    return {"count": len(rows), "suggestions": to_jsonable(rows)}


@router.get("/documents/{document_id}/tag-suggestions")
def list_tag_suggestions(
    document_id: str = ApiPath(...), revision_id: str | None = Query(None),
) -> dict[str, Any]:
    rows = _store().list(document_id, revision_id)
    return {"count": len(rows), "suggestions": to_jsonable(rows)}


@router.post("/tag-suggestions/{suggestion_id}/review")
def review_tag_suggestion(
    body: ReviewRequest,
    suggestion_id: str = ApiPath(...),
    reviewer: str = Header(..., alias="X-Actor-ID", min_length=1),
    services: ApplicationServices = Depends(get_application_services),
) -> dict[str, Any]:
    store = _store()
    try:
        suggestion = store.get(suggestion_id)
    except KeyError as exc:
        raise ChunkNotFoundError("tag suggestion not found or not accessible") from exc
    tag_id = body.resolved_tag_id or suggestion.existing_tag_id
    if body.approve:
        tag = services.db.get_tag(str(tag_id or ""))
        if tag is None or tag["collection_id"] != str(collection_uuid(suggestion.collection)):
            raise BadRequestError("approval requires a tag in the document collection")
    service = TagEnrichmentService(
        store, lambda *_: (), model=suggestion.model,
        prompt_version=suggestion.prompt_version, enabled=False,
    )
    try:
        reviewed = service.review(
            suggestion_id, approve=body.approve, reviewer=reviewer,
            resolved_tag_id=body.resolved_tag_id,
            apply_existing_tag=services.db.add_document_tag,
        )
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    return {"suggestion": to_jsonable(reviewed)}


@router.get("/revisions/{revision_id}/derived-artifacts")
def list_derived_artifacts(revision_id: str = ApiPath(...)) -> dict[str, Any]:
    rows = _derived_store().list(revision_id)
    return {"count": len(rows), "artifacts": to_jsonable(rows)}


@router.patch("/derived-artifacts/{artifact_id}")
def set_derived_artifact_enabled(
    body: DerivedEnabledRequest, artifact_id: str = ApiPath(...),
) -> dict[str, Any]:
    try:
        _derived_store().set_enabled(artifact_id, body.enabled)
    except KeyError as exc:
        raise ChunkNotFoundError("derived artifact not found or not accessible") from exc
    return {"artifact_id": artifact_id, "enabled": body.enabled}


@router.delete("/revisions/{revision_id}/derived-artifacts")
def delete_derived_artifacts(revision_id: str = ApiPath(...)) -> dict[str, int]:
    return {"deleted": _derived_store().delete_revision(revision_id)}


@router.get("/metrics/enrichment")
def enrichment_metrics(since: float = Query(0, ge=0)) -> dict[str, Any]:
    return _metrics_store().summary(since)


__all__ = ["router"]
