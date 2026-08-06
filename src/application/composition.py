"""
Application-services composition root.

One place that knows how to build the application services
(``QueryService`` / ``IngestionService`` / ``DocumentService`` /
``SystemService``) from settings + ``data_dir``, plus the
:class:`EngineCache` that dispatches per-collection engines.

M3 update
--------
The boot wiring now builds a
:class:`MultiCollectionVectorStore` (one ChromaDB persistent
client hosting many collections) and an :class:`EngineCache`
that lazily constructs per-collection ``HybridSearch`` /
``IngestionPipeline``. ``QueryService`` and ``IngestionService``
delegate to the cache so every request routes to the right
collection — fixing the v0.2 known limitation where non-default
collections silently ran on the default index.

CLI / MCP / Streamlit entry points that don't need the Web API's
multi-collection routing can still call
``scripts.query.build_query_components`` /
``scripts.ingest.build_pipeline`` directly; the cache is the Web
API's convenience wrapper, not a hard replacement.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.application.engines import EngineCache
    from src.core.settings import Settings
    from src.ingestion.document_manager import DocumentManager

__all__ = [
    "ApplicationServices",
    "build_application_services",
    "build_document_manager",
]


@dataclass
class ApplicationServices:
    """Handful of application services built at boot."""

    query: Any  # QueryService
    ingestion: Any  # IngestionService
    document: Any  # DocumentService
    system: Any  # SystemService
    trace: Any  # TraceStore — shared by QueryService / IngestionService
    engines: Any  # EngineCache — multi-collection routing
    db: Any | None = None  # WebApiDB — durable Web API metadata


def _load_settings(config_path: str | None) -> "Settings":
    from src.core.settings import Settings, load_settings

    if config_path and Path(config_path).is_file():
        return load_settings(config_path)
    # M4: no explicit path → prefer the repo's default config if present,
    # so ``python -m src.web_api.main`` boots against the real providers
    # (embedding base_url / keys) instead of bare ``Settings()`` defaults.
    default = Path("config/settings.yaml")
    if default.is_file():
        return load_settings(str(default))
    return Settings()


def build_document_manager(
    *,
    data_dir: str,
    settings: "Settings",
    vector_store: Any,
) -> "DocumentManager":
    """
    Build a :class:`DocumentManager` over the same on-disk layout that
    ``build_pipeline`` produces (``<data_dir>/db/...`` + images).

    Shared by the composition root and the Streamlit dashboard's page
    services so document lifecycle (list / detail / delete / stats)
    always talks to the same storage layout.

    M3 note: ``vector_store`` is expected to be a
    :class:`MultiCollectionVectorStore` so the document manager
    can scope ``get_by_metadata`` / ``delete_by_metadata`` to the
    right collection.
    """
    from src.ingestion.document_manager import DocumentManager
    from src.ingestion.embedding.sparse_encoder import SparseEncoder
    from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer
    from src.ingestion.storage.image_storage import ImageStorage
    from src.libs.loader.file_integrity import SQLiteIntegrityChecker

    db = Path(data_dir) / "db"
    sparse_encoder = SparseEncoder.from_settings(settings.sparse)
    bm25_indexer = BM25Indexer(
        persist_dir=str(db / "bm25"),
        sparse_encoder=sparse_encoder,
    )
    return DocumentManager(
        chroma_store=vector_store,
        bm25_indexer=bm25_indexer,
        bm25_index=BM25Index(),  # in-memory placeholder (same as dashboard)
        image_storage=ImageStorage(
            db_path=str(db / "image_index.db"),
            base_dir=str(Path(data_dir) / "images"),
        ),
        file_integrity=SQLiteIntegrityChecker(
            str(db / "ingestion_history.db"),
        ),
    )


def build_application_services(
    *,
    data_dir: str,
    config_path: str | None = None,
    collection: str = "default",  # legacy kw — ignored by EngineCache but
    #                       kept for CLI compatibility
    settings: "Settings | None" = None,
    splitter: Any | None = None,
    embedding: Any | None = None,
    vector_store: Any | None = None,
    llm: Any | None = None,
    upload_policy: Any | None = None,
) -> ApplicationServices:
    """Build the full application-service stack from settings + data_dir.

    Collaborators are injectable (same convention as
    ``build_pipeline`` / ``build_query_components``) so tests and
    entry points can substitute fakes. ``None`` collaborators are
    created from ``settings`` via the Libs factories.

    M3 update: builds a :class:`MultiCollectionVectorStore` router +
    an :class:`EngineCache`. The ``collection`` kwarg is honoured
    by priming the cache so the legacy boot-collection is
    constructed eagerly; the router + cache serve every
    collection regardless.
    """
    from src.application.engines import EngineCache
    from src.application.services import (
        DocumentService,
        IngestionService,
        QueryService,
        SystemService,
    )
    from src.application.services.trace_store import TraceStore
    from src.application.services.web_store import WebApiDB
    from src.libs.embedding import EmbeddingFactory
    from src.libs.splitter import SplitterFactory
    from src.libs.vector_store import VectorStoreFactory

    settings = settings or _load_settings(config_path)
    splitter = splitter or SplitterFactory.create(settings.splitter)
    embedding = embedding or EmbeddingFactory.create(settings.embedding)
    if vector_store is None:
        vector_store = VectorStoreFactory.create_multi_collection(
            settings.vector_store,
        )

    manager = build_document_manager(
        data_dir=data_dir,
        settings=settings,
        vector_store=vector_store,
    )
    trace_store = TraceStore(
        log_path=str(Path(data_dir) / "traces" / "traces.jsonl"),
    )
    # M3 batch 2: one SQLite store + one TaskTracker shared by the
    # ingestion and query paths — durable task status (survives
    # restart), indexed traces, async query results + last_query_id.
    from src.application.services.task_tracker import TaskTracker

    web_db = WebApiDB(str(Path(data_dir) / "db" / "web_api.db"))
    tracker = TaskTracker(db=web_db)
    engines = EngineCache(
        settings=settings,
        data_dir=data_dir,
        embedding=embedding,
        vector_store=vector_store,
        splitter=splitter,
        llm=llm,
    )
    # Prime the boot collection eagerly so a misconfigured index
    # raises at startup rather than on the first request. Other
    # collections lazy-build on first access.
    engines.prime([collection])

    return ApplicationServices(
        query=QueryService(
            engines,
            trace_store=trace_store,
            tracker=tracker,
            db=web_db,
        ),
        ingestion=IngestionService(
            engines,
            trace_store=trace_store,
            tracker=tracker,
            # Upload limits are injected (the Web API boot builds the
            # policy from its env-driven settings); None → app defaults.
            upload_policy=upload_policy,
        ),
        document=DocumentService(manager),
        system=SystemService(
            settings,
            embedding=embedding,
            vector_store=vector_store,
            db=web_db,
            data_dir=data_dir,
        ),
        trace=trace_store,
        engines=engines,
        db=web_db,
    )
