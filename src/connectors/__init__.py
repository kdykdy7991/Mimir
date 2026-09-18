"""Transport-neutral external datasource contracts."""

from src.connectors.contracts import (
    Connector, ConnectorItem, SourceDocument, SyncCheckpoint,
)
from src.connectors.store import CredentialCipher, DataSource, DataSourceStore
from src.connectors.http_security import SSRFPolicy, SafeHttpClient, SafeUrlValidator
from src.connectors.rss_url import ControlledUrlConnector, RssConnector
from src.connectors.conflicts import RevisionAwareDocumentApplier, SyncConflictStore
from src.connectors.service import DataSourceSyncService, sync_worker_handler
from src.connectors.ingestion_writer import IngestionDocumentWriter
from src.connectors.runtime import build_sync_worker_pool

__all__ = [
    "Connector", "ConnectorItem", "CredentialCipher", "DataSource",
    "DataSourceStore", "SourceDocument", "SyncCheckpoint",
    "SSRFPolicy", "SafeHttpClient", "SafeUrlValidator",
    "ControlledUrlConnector", "DataSourceSyncService", "IngestionDocumentWriter", "RssConnector",
    "RevisionAwareDocumentApplier", "SyncConflictStore", "sync_worker_handler",
    "build_sync_worker_pool",
]
