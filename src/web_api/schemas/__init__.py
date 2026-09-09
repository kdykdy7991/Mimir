"""
Re-exports for the schema layer.

Routers should import from this module so the public DTO surface has a
single, stable import path. Internal submodules are still importable
directly (e.g. for cross-references inside the layer).
"""

from src.web_api.schemas._types import ChunkId, Id, UtcDatetime
from src.web_api.schemas.collections import (
    CollectionCreateRequest,
    CollectionDetail,
    CollectionListResponse,
    CollectionSummary,
)
from src.web_api.schemas.common import (
    ErrorDetail,
    ErrorEnvelope,
    Page,
    PageInfo,
)
from src.web_api.schemas.documents import (
    DocumentDetail,
    DocumentListResponse,
    DocumentStatus,
    DocumentSummary,
    DocumentUploadResponse,
)
from src.web_api.schemas.errors import TaskError
from src.web_api.schemas.images import ImageMeta
from src.web_api.schemas.mcp_keys import (
    MCPKeyCollectionsUpdateRequest, MCPKeyCreateRequest, MCPKeyListResponse,
    MCPKeyMetadata, MCPKeySecretResponse,
)
from src.web_api.schemas.queries import (
    Citation,
    CitationImage,
    CitationScores,
    QueryDiagnostics,
    QueryMode,
    QueryRequest,
    QueryResponse,
)
from src.web_api.schemas.system import (
    DependencyHealth,
    ProviderStatus,
    SystemHealth,
    SystemInfo,
)
from src.web_api.schemas.tasks import (
    TaskProgress,
    TaskStage,
    TaskStatus,
    TaskStatusResponse,
)
from src.web_api.schemas.traces import TraceResponse, TraceStage

__all__ = [
    # types
    "ChunkId",
    "Id",
    "UtcDatetime",
    # common
    "ErrorDetail",
    "ErrorEnvelope",
    "Page",
    "PageInfo",
    # errors (async / business)
    "TaskError",
    # system
    "DependencyHealth",
    "ProviderStatus",
    "SystemHealth",
    "SystemInfo",
    # collections
    "CollectionCreateRequest",
    "CollectionDetail",
    "CollectionListResponse",
    "CollectionSummary",
    # documents
    "DocumentDetail",
    "DocumentListResponse",
    "DocumentStatus",
    "DocumentSummary",
    "DocumentUploadResponse",
    # tasks
    "TaskProgress",
    "TaskStage",
    "TaskStatus",
    "TaskStatusResponse",
    # queries
    "Citation",
    "CitationImage",
    "CitationScores",
    "QueryDiagnostics",
    "QueryMode",
    "QueryRequest",
    "QueryResponse",
    # mcp keys
    "MCPKeyCollectionsUpdateRequest",
    "MCPKeyCreateRequest",
    "MCPKeyListResponse",
    "MCPKeyMetadata",
    "MCPKeySecretResponse",
    # images
    "ImageMeta",
    # traces
    "TraceResponse",
    "TraceStage",
]
