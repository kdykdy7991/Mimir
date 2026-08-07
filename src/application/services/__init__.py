"""
Re-exports for the application service layer.

Single import path so routers / CLI / MCP / Streamlit can write
``from src.application.services import QueryService`` regardless of
how the internal modules are split.
"""

from src.application.services.document_service import DocumentService
from src.application.services.embedding_usage_store import EmbeddingUsageStore
from src.application.services.ingestion_service import IngestionService
from src.application.services.query_service import QueryResult, QueryService
from src.application.services.task_types import (
    TaskError,
    TaskProgress,
    TaskStage,
    TaskStatus,
)
from src.application.services.task_tracker import TaskRecord, TaskTracker
from src.application.services.upload_types import (
    BatchFileResult,
    BatchFileStatus,
    BatchFileUpload,
    BatchUploadResponse,
    UploadPolicy,
)
from src.application.services.system_service import (
    DependencyHealthView,
    ProviderStatusView,
    SystemHealthView,
    SystemInfoView,
    SystemService,
)

__all__ = [
    # query
    "QueryResult",
    "QueryService",
    # ingestion
    "BatchFileUpload",
    "IngestionService",
    # task / upload contract types (M5: application-layer DTOs)
    "BatchFileResult",
    "BatchFileStatus",
    "BatchUploadResponse",
    "TaskError",
    "TaskProgress",
    "TaskStage",
    "TaskStatus",
    "UploadPolicy",
    # document
    "DocumentService",
    # embedding token usage (PRD docs/prd-embedding-token-metrics.md)
    "EmbeddingUsageStore",
    # system
    "DependencyHealthView",
    "ProviderStatusView",
    "SystemHealthView",
    "SystemInfoView",
    "SystemService",
    # task tracking (M2 batch 2)
    "TaskRecord",
    "TaskTracker",
]
