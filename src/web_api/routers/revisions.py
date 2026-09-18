"""Management REST endpoints for optimistic chunk revision edits."""

from __future__ import annotations

from pathlib import Path
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, Path as ApiPath, status
from pydantic import BaseModel, Field

from src.application.composition import ApplicationServices
from src.application.contracts import to_jsonable
from src.application.services.revision_service import (
    RevisionNotFoundOrAccessibleError,
    RevisionService,
)
from src.application.services.revision_indexer import (
    RevisionIndexCoordinator,
    RevisionIndexingError,
)
from src.application.services.revision_index_participants import (
    BM25RevisionParticipant,
    DenseRevisionParticipant,
    ParentChildRevisionParticipant,
    DerivedRevisionParticipant,
)
from src.ingestion.storage import (
    DerivedContentStore,
    EnrichmentMetricsStore,
    RevisionConflictError,
    RevisionStore,
)
from src.application.services.derived_content_service import DerivedContentService
from src.mcp_server.auth.context import TrustedLocalPrincipal
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
from src.web_api.dependencies import DEFAULT_DATA_DIR, get_application_services
from src.web_api.errors import BadRequestError, ChunkNotFoundError, ConflictError

router = APIRouter(prefix="/documents", tags=["knowledge-revisions"])


class ChunkEditRequest(BaseModel):
    base_revision_id: str = Field(min_length=1)
    text: str | None = None
    metadata_patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=500)


class ChunkRollbackRequest(BaseModel):
    base_revision_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


def _service(services: ApplicationServices) -> RevisionService:
    client = InProcessRagReadOnlyClient(
        data_dir=str(DEFAULT_DATA_DIR), services=services,
    )


def _coordinator(
    services: ApplicationServices, revision_store: RevisionStore,
    collection: str,
) -> RevisionIndexCoordinator:
    pipeline = services.engines.pipeline_for(collection)
    client = InProcessRagReadOnlyClient(
        data_dir=str(DEFAULT_DATA_DIR), services=services,
    )
    participants: list[Any] = [
        DenseRevisionParticipant(
            revision_store, pipeline.vector_upserter.store,
            services.engines.embedding,
        ),
        BM25RevisionParticipant(pipeline.bm25_indexer),
    ]
    if pipeline.parent_chunk_store is not None and pipeline.parent_chunk_builder is not None:
        participants.append(ParentChildRevisionParticipant(
            pipeline.parent_chunk_store, pipeline.parent_chunk_builder,
            client.get_revision_document_chunks,
        ))
    llm = services.engines.llm
    if services.engines.settings.derived_content_enabled and llm is not None:
        def generate_derived(text: str) -> dict[str, Any]:
            prompt = (
                "Return JSON only with keys summary (string) and questions "
                "(array of strings). Derived questions must be answerable from "
                "the source.\nSource:\n" + text[:12_000]
            )
            metrics = EnrichmentMetricsStore(
                Path(DEFAULT_DATA_DIR) / "db" / "enrichment_metrics.db",
            )
            started = time.perf_counter()
            raw = None
            try:
                raw = llm.chat([{"role": "user", "content": prompt}], temperature=0)
                value = json.loads(str(raw).strip())
                if not isinstance(value, dict):
                    raise ValueError("derived model output must be a JSON object")
                metrics.record(
                    operation="derived", model=str(getattr(llm, "model", type(llm).__name__)),
                    prompt_version="derived-v1",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    input_chars=len(prompt), output_chars=len(str(raw)), success=True,
                )
                return value
            except Exception as exc:
                metrics.record(
                    operation="derived", model=str(getattr(llm, "model", type(llm).__name__)),
                    prompt_version="derived-v1",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    input_chars=len(prompt),
                    output_chars=(len(str(raw)) if raw is not None else None),
                    success=False, error_type=type(exc).__name__,
                )
                raise

        participants.append(DerivedRevisionParticipant(DerivedContentService(
            DerivedContentStore(Path(DEFAULT_DATA_DIR) / "db" / "derived.db"),
            generate_derived,
            model=str(getattr(llm, "model", type(llm).__name__)),
            prompt_version="derived-v1", enabled=True,
        )))
    return RevisionIndexCoordinator(revision_store, participants)
    return RevisionService(
        RevisionStore(Path(DEFAULT_DATA_DIR) / "db" / "revisions.db"),
        client.get_authorized_chunk_snapshot,
    )


def _etag_value(if_match: str) -> str:
    value = if_match.strip()
    if value.startswith("W/"):
        value = value[2:].strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if not value or value == "*":
        raise BadRequestError("If-Match must contain one base revision ID")
    return value


@router.post(
    "/{document_id}/chunks/{chunk_id}/revisions",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a pending chunk revision",
)
def create_chunk_revision(
    body: ChunkEditRequest,
    document_id: str = ApiPath(...),
    chunk_id: str = ApiPath(...),
    if_match: str = Header(..., alias="If-Match"),
    actor: str = Header(..., alias="X-Actor-ID", min_length=1),
    services: ApplicationServices = Depends(get_application_services),
) -> dict[str, Any]:
    etag = _etag_value(if_match)
    if etag != body.base_revision_id:
        raise ConflictError(
            "If-Match and base_revision_id do not match",
            details={"expected_base_revision_id": body.base_revision_id},
        )
    try:
        revision = _service(services).create_edit(
            document_id, chunk_id, TrustedLocalPrincipal(),
            base_revision_id=body.base_revision_id,
            actor=actor, reason=body.reason, text=body.text,
            metadata_patch=body.metadata_patch,
        )
    except RevisionConflictError as exc:
        raise ConflictError("base revision is not current") from exc
    except RevisionNotFoundOrAccessibleError as exc:
        raise ChunkNotFoundError("chunk not found or not accessible") from exc
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return {
        "revision": to_jsonable(revision),
        "status": "pending_index",
        "is_current": False,
    }


@router.get(
    "/{document_id}/chunks/{chunk_id}/revisions",
    summary="List chunk revision history",
)
def list_chunk_revision_history(
    document_id: str = ApiPath(...), chunk_id: str = ApiPath(...),
    services: ApplicationServices = Depends(get_application_services),
) -> dict[str, Any]:
    try:
        rows = _service(services).list_history(
            document_id, chunk_id, TrustedLocalPrincipal(),
        )
    except RevisionNotFoundOrAccessibleError as exc:
        raise ChunkNotFoundError("chunk not found or not accessible") from exc
    return {"count": len(rows), "revisions": to_jsonable(rows)}


@router.post(
    "/{document_id}/chunks/{chunk_id}/revisions/{revision_id}/rebuild",
    summary="Rebuild indexes and activate a pending revision",
)
def rebuild_chunk_revision(
    document_id: str = ApiPath(...),
    chunk_id: str = ApiPath(...),
    revision_id: str = ApiPath(...),
    services: ApplicationServices = Depends(get_application_services),
) -> dict[str, Any]:
    store = RevisionStore(Path(DEFAULT_DATA_DIR) / "db" / "revisions.db")
    try:
        revision = store.get(revision_id)
        if (revision.document_id, revision.chunk_id) != (document_id, chunk_id):
            raise ChunkNotFoundError("revision not found or not accessible")
        result = _coordinator(
            services, store, revision.collection,
        ).rebuild_and_activate(revision_id)
    except RevisionIndexingError as exc:
        raise ConflictError(
            "revision index rebuild failed; previous revision remains active",
            details={
                "revision_id": revision_id,
                "failed_participant": exc.result.failed_participant,
                "error_type": exc.result.error_type,
            },
        ) from exc
    except (RevisionConflictError, ValueError) as exc:
        raise ConflictError(str(exc)) from exc
    except ChunkNotFoundError:
        raise
    except Exception as exc:
        raise ChunkNotFoundError("revision not found or not accessible") from exc
    services.engines.invalidate_collection(revision.collection)
    return {
        "revision_id": result.revision_id,
        "status": "active",
        "participants": list(result.participants),
    }


@router.post(
    "/{document_id}/chunks/{chunk_id}/revisions/{target_revision_id}/rollback",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a pending rollback revision",
)
def rollback_chunk_revision(
    body: ChunkRollbackRequest,
    document_id: str = ApiPath(...),
    chunk_id: str = ApiPath(...),
    target_revision_id: str = ApiPath(...),
    if_match: str = Header(..., alias="If-Match"),
    actor: str = Header(..., alias="X-Actor-ID", min_length=1),
    services: ApplicationServices = Depends(get_application_services),
) -> dict[str, Any]:
    etag = _etag_value(if_match)
    if etag != body.base_revision_id:
        raise ConflictError("If-Match and base_revision_id do not match")
    try:
        revision = _service(services).create_rollback(
            document_id, chunk_id, TrustedLocalPrincipal(),
            base_revision_id=body.base_revision_id,
            target_revision_id=target_revision_id,
            actor=actor, reason=body.reason,
        )
    except RevisionConflictError as exc:
        raise ConflictError("base revision is not current") from exc
    except RevisionNotFoundOrAccessibleError as exc:
        raise ChunkNotFoundError("revision not found or not accessible") from exc
    return {
        "revision": to_jsonable(revision),
        "status": "pending_index",
        "is_current": False,
    }


__all__ = ["router"]
