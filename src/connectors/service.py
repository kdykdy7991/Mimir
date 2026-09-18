"""Production orchestration for connection checks and atomic sync runs."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Literal, Protocol

import httpx

from src.application.services.worker_pool import PermanentWorkerError, RetryableWorkerError
from src.connectors.contracts import Connector, SourceDocument, SyncCheckpoint
from src.connectors.http_security import (
    DownloadLimitError,
    SafeHttpClient,
    UnsafeUrlError,
)
from src.connectors.rss_url import ControlledUrlConnector, RssConnector
from src.connectors.store import CheckpointConflictError, DataSource, DataSourceStore

SyncOutcome = Literal["added", "updated", "deleted", "conflict", "unchanged"]
DocumentApplier = Callable[[DataSource, SourceDocument], SyncOutcome]
ConnectorBuilder = Callable[[DataSource, dict[str, object]], Connector]


class SyncHeartbeat(Protocol):
    def heartbeat(self) -> bool: ...


class DataSourceSyncError(RuntimeError):
    code = "sync_failed"


class DataSourceDisabledError(PermanentWorkerError):
    code = "data_source_disabled"


class SyncPermanentError(PermanentWorkerError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SyncRetryableError(RetryableWorkerError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def default_connector_builder(source: DataSource, credentials: dict[str, object]) -> Connector:
    """Build only the two frozen Task 08 connectors from persisted policy.

    Authentication schemes are intentionally not guessed. A configured
    credential without an explicit supported transport is rejected rather
    than silently ignored or leaked across redirects.
    """
    unknown_credentials = set(credentials) - {"token"}
    if unknown_credentials:
        raise PermanentWorkerError("unsupported connector credentials")
    raw_url = source.policy.get("url") or source.policy.get("feed_url")
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise PermanentWorkerError("connector policy requires url")
    token = credentials.get("token")
    if token is not None and (not isinstance(token, str) or not token.strip()):
        raise PermanentWorkerError("connector token is invalid")
    client = SafeHttpClient(
        authorization=f"Bearer {token.strip()}" if isinstance(token, str) else None,
    )
    if source.connector_type == "rss":
        return RssConnector(raw_url, client)
    if source.connector_type == "url":
        return ControlledUrlConnector(raw_url, client)
    client.close()
    raise PermanentWorkerError("unsupported connector type")


class DataSourceSyncService:
    def __init__(
        self, store: DataSourceStore, *,
        connector_builder: ConnectorBuilder = default_connector_builder,
    ) -> None:
        self.store = store
        self.connector_builder = connector_builder

    def test_connection(self, source_id: str) -> None:
        source = self.store.get(source_id)
        connector = self._build(source)
        try:
            connector.test()
            # Exercise the bounded transport and format parser, but never
            # apply documents or persist the connector's candidate checkpoint.
            tuple(connector.list(self._checkpoint(source)))
        finally:
            connector.close()

    def run(
        self, source_id: str, apply_document: DocumentApplier, *,
        heartbeat: SyncHeartbeat | None = None,
    ) -> dict[str, int | str | float | None]:
        source = self.store.get(source_id)
        if not source.enabled:
            raise DataSourceDisabledError("data source is paused")
        started = time.time()
        run_id = self.store.record_run(
            source_id=source_id, status="running", started_at=started,
        )
        counts = {"added": 0, "updated": 0, "deleted": 0, "conflict": 0}
        connector = self._build(source)
        try:
            checkpoint = self._checkpoint(source)
            for item in connector.list(checkpoint):
                if heartbeat is not None and not heartbeat.heartbeat():
                    raise RetryableWorkerError("sync worker lost its lease")
                outcome = apply_document(source, connector.fetch(item))
                if outcome in counts:
                    counts[outcome] += 1
            next_checkpoint = connector.checkpoint()
            self.store.update_checkpoint(
                source_id, self._checkpoint_payload(next_checkpoint),
                expected_revision=source.checkpoint_revision,
            )
        except Exception as exc:
            self.store.finish_run(
                run_id, status="failed", finished_at=time.time(),
                error_code=self._error_code(exc), **self._count_args(counts),
            )
            raise
        finally:
            connector.close()
        status = "conflict" if counts["conflict"] else "succeeded"
        finished = time.time()
        self.store.finish_run(
            run_id, status=status, finished_at=finished,
            **self._count_args(counts),
        )
        return {"run_id": run_id, "status": status, "started_at": started,
                "finished_at": finished, **counts}

    def _build(self, source: DataSource) -> Connector:
        return self.connector_builder(source, self.store.credentials(source.id))

    @staticmethod
    def _checkpoint(source: DataSource) -> SyncCheckpoint | None:
        if not source.checkpoint:
            return None
        updated = source.checkpoint.get("updated_at")
        return SyncCheckpoint(
            cursor=source.checkpoint.get("cursor"),
            revision=int(source.checkpoint.get("revision", 0)),
            updated_at=(datetime.fromisoformat(updated) if isinstance(updated, str) else None),
        )

    @staticmethod
    def _checkpoint_payload(checkpoint: SyncCheckpoint) -> dict[str, object]:
        return {
            "cursor": checkpoint.cursor,
            "revision": checkpoint.revision,
            "updated_at": (
                checkpoint.updated_at.astimezone(timezone.utc).isoformat()
                if checkpoint.updated_at else None
            ),
        }

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, UnsafeUrlError):
            return "unsafe_url"
        if isinstance(exc, DownloadLimitError):
            return "download_limit"
        if isinstance(exc, CheckpointConflictError):
            return "checkpoint_conflict"
        if isinstance(exc, httpx.TimeoutException):
            return "upstream_timeout"
        return str(getattr(exc, "code", "sync_failed"))

    @staticmethod
    def _count_args(counts: dict[str, int]) -> dict[str, int]:
        return {
            "added": counts["added"], "updated": counts["updated"],
            "deleted": counts["deleted"], "conflicts": counts["conflict"],
        }


def sync_worker_handler(
    service: DataSourceSyncService, apply_document: DocumentApplier,
):
    def handle(payload: dict[str, object], context: SyncHeartbeat) -> None:
        source_id = payload.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise PermanentWorkerError("sync payload requires source_id")
        try:
            service.run(source_id, apply_document, heartbeat=context)
        except PermanentWorkerError:
            raise
        except (UnsafeUrlError, DownloadLimitError, ValueError, KeyError) as exc:
            raise SyncPermanentError(service._error_code(exc)) from exc
        except Exception as exc:
            raise SyncRetryableError(service._error_code(exc)) from exc
    return handle


__all__ = [
    "DataSourceDisabledError", "DataSourceSyncError", "DataSourceSyncService",
    "DocumentApplier", "SyncOutcome", "default_connector_builder",
    "SyncPermanentError", "SyncRetryableError", "sync_worker_handler",
]
