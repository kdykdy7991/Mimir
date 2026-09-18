"""Production composition for the durable datasource sync worker."""

from __future__ import annotations

from pathlib import Path

from src.application.composition import ApplicationServices
from src.application.services.worker_pool import DurableWorkerPool, DurableWorkerStore
from src.connectors.conflicts import RevisionAwareDocumentApplier, SyncConflictStore
from src.connectors.ingestion_writer import IngestionDocumentWriter
from src.connectors.service import DataSourceSyncService, sync_worker_handler
from src.connectors.store import CredentialCipher, DataSourceStore
from src.ingestion.storage.parent_chunk_store import ParentChunkStore


def build_sync_worker_pool(
    services: ApplicationServices, *, data_dir: str | Path,
    owner: str, lease_seconds: float = 30.0,
) -> DurableWorkerPool:
    """Build the real sync handler without starting background scheduling.

    Task 08 deliberately leaves scheduling disabled by default. An explicit
    process/command may call ``run_once('sync')`` on the returned pool.
    """
    root = Path(data_dir)
    db = root / "db"
    sources = DataSourceStore(
        db / "data_sources.db", CredentialCipher.from_env(),
    )
    conflicts = SyncConflictStore(db / "sync_conflicts.db")
    writer = IngestionDocumentWriter(
        services.ingestion, services.document,
        ParentChunkStore(db / "parent_chunks.db"),
    )
    applier = RevisionAwareDocumentApplier(
        conflicts, writer.resolve_current, writer,
        document_identity=writer.document_identity,
    )
    return DurableWorkerPool(
        DurableWorkerStore(db / "worker_jobs.db"),
        {"sync": sync_worker_handler(DataSourceSyncService(sources), applier)},
        owner=owner, lease_seconds=lease_seconds,
    )


__all__ = ["build_sync_worker_pool"]
