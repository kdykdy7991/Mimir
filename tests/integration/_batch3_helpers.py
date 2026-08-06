"""
Shared fakes + services builder for the M2 batch 3 integration tests
(queries / traces / images).

Not a test module itself — the three ``test_web_api_*`` files import the
``FakeHybridSearch`` / ``_FakePipeline`` and ``build_batch3_services``
helper so the 12 cases stay focused on endpoint behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.application.composition import ApplicationServices
from src.application.services import DocumentService, IngestionService, SystemService
from src.application.services.query_service import QueryService
from src.application.services.task_tracker import TaskTracker
from src.application.services.trace_store import TraceStore
from src.core.query_engine.query_processor import QueryProcessor
from src.core.settings import Settings
from src.core.types import ChunkRecord, RetrievalResult
from src.ingestion.document_manager import DocumentManager
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage.bm25_indexer import BM25Index, BM25Indexer
from src.libs.embedding import EmbeddingError
from tests.unit.test_document_manager import (
    FakeChromaStore,
    FakeImageStorage,
    FakeIntegrity,
)

# Sentinel: "wire the shared trace store into ingestion" (the default).
_USE_SHARED = object()


def retrieval_result(
    chunk_id: str,
    source_path: str,
    *,
    score: float = 0.5,
    source: str = "fusion",
    page: int | None = None,
    images: list | None = None,
    text: str = "chunk text here",
) -> RetrievalResult:
    """A retrievable chunk carrying the metadata the citation mapper needs."""
    return RetrievalResult(
        chunk=ChunkRecord(
            id=chunk_id,
            text=text,
            metadata={
                "source_path": source_path,
                "page": page,
                "images": images or [],
            },
            start_offset=0,
            end_offset=0,
        ),
        score=score,
        rank=1,
        source=source,
    )


class FakeDenseRetriever:
    """Dense path for the fake engine — can be told to raise EmbeddingError."""

    name = "dense_retriever"

    def __init__(self, results=None, *, raise_error=None) -> None:
        self._results = results or []
        self.raise_error = raise_error

    def retrieve(self, query, top_k, filters=None, trace=None):
        if trace is not None:
            trace.record_stage(self.name, event="start", top_k=top_k)
        if self.raise_error is not None:
            if trace is not None:
                trace.record_stage(self.name, event="embedding_error")
            raise self.raise_error
        if trace is not None:
            trace.record_stage(self.name, event="finish", n_out=len(self._results))
        return list(self._results)


class FakeSparseRetriever:
    """Sparse path for the fake engine."""

    name = "sparse_retriever"

    def __init__(self, results=None) -> None:
        self._results = results or []

    def retrieve(self, keywords, top_k, trace=None):
        if trace is not None:
            trace.record_stage(self.name, event="finish", n_out=len(self._results))
        return list(self._results)


class FakeHybridSearch:
    """Stand-in for ``HybridSearch``.

    Mirrors the orchestrator-level trace stages the real engine emits
    (``query_processing`` / ``dense_retrieval`` / ``sparse_retrieval`` /
    ``fusion`` / ``trim``) plus the retriever ``finish`` events, so the
    service's diagnostics counting and the trace endpoint are exercised
    for real.
    """

    def __init__(self, results=None, *, dense_raises=None) -> None:
        self._results = results or []
        self.calls: list[dict] = []
        self.dense = FakeDenseRetriever(self._results, raise_error=dense_raises)
        self.sparse = FakeSparseRetriever(self._results)
        self.query_processor = QueryProcessor(SparseEncoder())
        self.retrieval_multiplier = 2

    def search(self, query, top_k=10, filters=None, trace=None):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        if trace is not None:
            n = len(self._results)
            trace.record_stage("query_processing", method="rule", elapsed_ms=0.4)
            trace.record_stage(
                "dense_retrieval", method="FakeDenseRetriever",
                elapsed_ms=1.0, fetch_k=top_k * 2,
            )
            trace.record_stage(
                "sparse_retrieval", method="FakeSparseRetriever",
                elapsed_ms=1.0, fetch_k=top_k * 2,
            )
            trace.record_stage("dense_retriever", event="finish", n_out=n)
            trace.record_stage("sparse_retriever", event="finish", n_out=n)
            if n:
                trace.record_stage(
                    "fusion", method="rrf_k=60", elapsed_ms=0.2,
                    n_dense=n, n_sparse=n,
                )
                trace.record_stage(
                    "trim", method="top_k_slice", top_k=top_k,
                    n_before=n, elapsed_ms=0.1,
                )
            else:
                trace.record_stage("hybrid_search", event="no_results")
        return list(self._results)


class FakePipeline:
    """Stand-in for ``IngestionPipeline``: records stages, never fails."""

    def __init__(self) -> None:
        self.runs: list[dict] = []

    def run(self, path, *, on_progress=None, trace=None, collection=None, source_path=None):
        self.runs.append({
            "path": str(path), "trace": trace is not None,
            "collection": collection,
        })
        if trace is not None:
            trace.record_stage("load", method="PdfLoader", elapsed_ms=1.0)
            trace.record_stage("split", method="MarkdownSplitter", elapsed_ms=1.0)
            trace.record_stage("embed", method="OpenAIEmbedding", elapsed_ms=2.0)
            trace.record_stage("upsert", method="ChromaStore", elapsed_ms=1.0)
        if on_progress is not None:
            on_progress("load", 1, 1)


def build_batch3_services(
    tmp_path: Path,
    *,
    hybrid: FakeHybridSearch | None = None,
    pipeline: FakePipeline | None = None,
    image_storage=None,
    ingestion_trace_store=_USE_SHARED,
    db=None,
    manager: DocumentManager | None = None,
) -> ApplicationServices:
    """Assemble application services over the same fakes as batch 1.

    ``hybrid`` / ``pipeline`` / ``image_storage`` are injectable so each
    test can shape its failure mode. ``ingestion_trace_store`` defaults to
    the shared store; pass ``None`` explicitly to simulate a task whose
    trace was never recorded.

    ``db`` (a :class:`WebApiDB`) enables the M3 batch 2 durable wiring:
    a shared SQLite-backed ``TaskTracker`` is passed to both services so
    async query tasks + ingestion tasks persist and survive a "restart".
    When ``None`` the legacy in-memory wiring is used.
    """
    bm25_dir = tmp_path / "db" / "bm25"
    bm25_dir.mkdir(parents=True, exist_ok=True)
    (bm25_dir / "default.json").write_text(
        json.dumps({"n_docs": 0, "avgdl": 0.0, "k1": 1.5, "b": 0.75, "terms": {}}),
        encoding="utf-8",
    )

    trace_store = TraceStore(
        log_path=tmp_path / "traces" / "traces.jsonl", db=db,
    )
    if ingestion_trace_store is _USE_SHARED:
        ingestion_trace_store = trace_store
    tracker = TaskTracker(db=db)

    manager = manager or DocumentManager(
        chroma_store=FakeChromaStore(),
        bm25_indexer=BM25Indexer(
            persist_dir=str(bm25_dir),
            sparse_encoder=SparseEncoder(),
        ),
        bm25_index=BM25Index(),
        image_storage=image_storage or FakeImageStorage(),
        file_integrity=FakeIntegrity(),
    )
    return ApplicationServices(
        query=QueryService(
            hybrid or FakeHybridSearch(),
            trace_store=trace_store,
            tracker=tracker,
            db=db,
        ),
        ingestion=IngestionService(
            pipeline or FakePipeline(),
            upload_dir=tmp_path / "uploads",
            trace_store=ingestion_trace_store,
            tracker=tracker,
        ),
        document=DocumentService(manager),
        system=SystemService(Settings()),
        trace=trace_store,
        engines=None,
    )


__all__ = [
    "EmbeddingError",
    "FakeDenseRetriever",
    "FakeHybridSearch",
    "FakePipeline",
    "FakeSparseRetriever",
    "build_batch3_services",
    "retrieval_result",
]
