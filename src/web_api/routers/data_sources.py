"""Trusted-admin datasource configuration and read-only sync status."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from src.connectors.store import CredentialCipher, DataSource, DataSourceStore
from src.connectors.conflicts import SyncConflictStore
from src.connectors.service import DataSourceSyncService
from src.application.services.worker_pool import DurableWorkerStore
from src.web_api.dependencies import DEFAULT_DATA_DIR
from src.web_api.errors import NotFoundError
from src.web_api.errors import BadRequestError, UpstreamError
from src.connectors.http_security import DownloadLimitError, UnsafeUrlError
from src.application.services.worker_pool import PermanentWorkerError
import httpx

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    connector_type: str = Field(pattern="^(rss|url)$")
    collection_id: str = Field(min_length=1)
    policy: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)


def _store() -> DataSourceStore:
    return DataSourceStore(
        Path(DEFAULT_DATA_DIR) / "db" / "data_sources.db",
        CredentialCipher.from_env(),
    )


def _conflict_store() -> SyncConflictStore:
    return SyncConflictStore(Path(DEFAULT_DATA_DIR) / "db" / "sync_conflicts.db")


def _sync_service(store: DataSourceStore = Depends(_store)) -> DataSourceSyncService:
    return DataSourceSyncService(store)


def _worker_store() -> DurableWorkerStore:
    return DurableWorkerStore(Path(DEFAULT_DATA_DIR) / "db" / "worker_jobs.db")


def _conflict_view(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key) for key in (
            "id", "source_id", "external_id", "document_id",
            "active_revision_id", "last_synced_revision_id", "remote_revision",
            "status", "created_at", "resolved_at", "resolution",
        )
    }


def _view(source: DataSource) -> dict[str, Any]:
    return {
        "id": source.id, "name": source.name,
        "connector_type": source.connector_type,
        "collection_id": source.collection_id, "policy": source.policy,
        "checkpoint": source.checkpoint,
        "checkpoint_revision": source.checkpoint_revision,
        "enabled": source.enabled, "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_data_source(body: DataSourceCreate, store: DataSourceStore = Depends(_store)) -> dict:
    return _view(store.create(
        name=body.name, connector_type=body.connector_type,
        collection_id=body.collection_id, policy=body.policy,
        credentials=body.credentials,
    ))


@router.get("")
def list_data_sources(
    collection_id: str | None = None, store: DataSourceStore = Depends(_store),
) -> dict:
    items = [_view(item) for item in store.list(collection_id=collection_id)]
    return {"count": len(items), "data_sources": items}


@router.get("/{source_id}/status")
def get_sync_status(source_id: str, store: DataSourceStore = Depends(_store)) -> dict:
    try:
        value = store.sync_status(source_id)
    except KeyError as exc:
        raise NotFoundError("data source not found") from exc
    return {"data_source": _view(value["source"]), "last_run": value["last_run"]}


@router.post("/{source_id}/test")
def test_data_source_connection(
    source_id: str, service: DataSourceSyncService = Depends(_sync_service),
) -> dict:
    try:
        service.test_connection(source_id)
    except KeyError as exc:
        raise NotFoundError("data source not found") from exc
    except (UnsafeUrlError, DownloadLimitError, PermanentWorkerError, ValueError) as exc:
        raise BadRequestError(
            "data source connection policy or configuration is invalid",
            details={"reason": DataSourceSyncService._error_code(exc)},
        ) from exc
    except httpx.TimeoutException as exc:
        raise UpstreamError(
            "data source connection timed out",
            details={"reason": "upstream_timeout"},
        ) from exc
    except Exception as exc:
        raise UpstreamError(
            "data source connection failed",
            details={"reason": DataSourceSyncService._error_code(exc)},
        ) from exc
    return {"ok": True, "data_source_id": source_id}


@router.post("/{source_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def enqueue_data_source_sync(
    source_id: str, store: DataSourceStore = Depends(_store),
    workers: DurableWorkerStore = Depends(_worker_store),
) -> dict:
    """Queue one explicit sync; the HTTP process never fetches remote content."""
    try:
        source = store.get(source_id)
    except KeyError as exc:
        raise NotFoundError("data source not found") from exc
    if not source.enabled:
        raise BadRequestError("data source is paused")
    idempotency_key = f"{source.id}:{source.checkpoint_revision}"
    task_id = str(uuid5(NAMESPACE_URL, f"skdy-sync:{idempotency_key}"))
    enqueued = workers.enqueue(
        task_id, "sync", {"source_id": source.id},
        idempotency_key=idempotency_key,
    )
    return {"task_id": task_id, "enqueued": enqueued}


@router.get("/{source_id}/failures")
def list_sync_failures(
    source_id: str, limit: int = Query(50, ge=1, le=100),
    store: DataSourceStore = Depends(_store),
) -> dict:
    try:
        items = store.list_failures(source_id, limit=limit)
    except KeyError as exc:
        raise NotFoundError("data source not found") from exc
    return {"count": len(items), "failures": items}


@router.get("/{source_id}/dead-letters")
def list_sync_dead_letters(
    source_id: str, limit: int = Query(50, ge=1, le=100),
    store: DataSourceStore = Depends(_store),
    workers: DurableWorkerStore = Depends(_worker_store),
) -> dict:
    import json
    try:
        store.get(source_id)
    except KeyError as exc:
        raise NotFoundError("data source not found") from exc
    items = []
    for row in workers.list_dead_letters("sync", limit=100):
        payload = json.loads(row["payload_json"])
        if payload.get("source_id") != source_id:
            continue
        items.append({key: row.get(key) for key in (
            "task_id", "queue", "state", "attempt", "max_attempts",
            "error_class", "error_code", "created_at", "updated_at",
        )})
        if len(items) >= limit:
            break
    return {"count": len(items), "dead_letters": items}


@router.post("/{source_id}/dead-letters/{task_id}/replay")
def replay_sync_dead_letter(
    source_id: str, task_id: str,
    store: DataSourceStore = Depends(_store),
    workers: DurableWorkerStore = Depends(_worker_store),
) -> dict:
    import json
    try:
        store.get(source_id)
    except KeyError as exc:
        raise NotFoundError("data source not found") from exc
    row = workers.get(task_id)
    if (
        row is None or row["queue"] != "sync"
        or json.loads(row["payload_json"]).get("source_id") != source_id
    ):
        raise NotFoundError("dead letter not found")
    replayed = workers.replay_dead_letter(task_id)
    return {"task_id": task_id, "replayed": replayed}


@router.get("/{source_id}/conflicts")
def list_sync_conflicts(
    source_id: str,
    store: DataSourceStore = Depends(_store),
    conflicts: SyncConflictStore = Depends(_conflict_store),
) -> dict:
    try:
        store.get(source_id)
    except KeyError as exc:
        raise NotFoundError("data source not found") from exc
    items = [_conflict_view(item) for item in conflicts.list_pending(source_id)]
    return {"count": len(items), "conflicts": items}


@router.post("/{source_id}/conflicts/{conflict_id}/acknowledge")
def acknowledge_sync_conflict(
    source_id: str, conflict_id: str,
    store: DataSourceStore = Depends(_store),
    conflicts: SyncConflictStore = Depends(_conflict_store),
) -> dict:
    try:
        store.get(source_id)
        return _conflict_view(conflicts.acknowledge(source_id, conflict_id))
    except KeyError as exc:
        raise NotFoundError("data source or conflict not found") from exc


@router.post("/{source_id}/pause")
def pause_data_source(source_id: str, store: DataSourceStore = Depends(_store)) -> dict:
    try:
        return _view(store.set_enabled(source_id, False))
    except KeyError as exc:
        raise NotFoundError("data source not found") from exc


@router.post("/{source_id}/resume")
def resume_data_source(source_id: str, store: DataSourceStore = Depends(_store)) -> dict:
    try:
        return _view(store.set_enabled(source_id, True))
    except KeyError as exc:
        raise NotFoundError("data source not found") from exc


__all__ = ["router"]
