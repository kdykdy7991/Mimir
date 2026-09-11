"""
``InProcessRagReadOnlyClient`` — the migration-default read client (P1.2).

Reuses the existing application services (``QueryService`` /
``DocumentService`` / settings) exactly the way the three legacy MCP tools
used to build their own stacks, but now that logic lives *inside the
client*. The MCP handlers become thin: validate input, call the client,
format output.

The Phase-2 contracts (evidence-based query output, canonical
``list_collections``) intentionally keep the legacy shapes here for
Phase-1 output parity; Phase 2 introduces the new contracts on top.

The client is a *read* boundary: there is deliberately no method that can
create / update / delete knowledge.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.core.settings import Settings, load_settings

from src.ingestion.chunk_order import (
    chunk_id_of,
    chunk_sort_key,
    page_number_of,
    stable_order_chunks,
)
from src.mcp_server.auth.authorization import (
    CollectionAccessDenied,
    filter_accessible_collections,
    require_collection_access,
)
from src.mcp_server.auth.context import AccessPrincipalLike
from src.mcp_server.clients.errors import (
    AccessDeniedError,
    InvalidRequestError,
    ResourceNotFoundError,
    UpstreamUnavailableError,
)
from src.mcp_server.clients.models import (
    CollectionInfo,
    Diagnostics,
    DocumentChunk,
    DocumentChunkPage,
    DocumentInfo,
    EvidenceItem,
    KnowledgeQueryResult,
    QueryRequest,
)

logger = logging.getLogger(__name__)


class InProcessRagReadOnlyClient:
    """Read-only client over the in-process application services.

    Collaborators are injectable for tests; when none are supplied the
    client lazily builds the application-service stack from ``config_path``
    + ``data_dir`` (cached once per instance).
    """

    def __init__(
        self,
        *,
        config_path: str | None = None,
        data_dir: str = "./data",
        services: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._config_path = config_path
        self._data_dir = data_dir
        self._injected_services = services
        self._settings = settings
        self._services: Any | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lazy service bootstrap
    # ------------------------------------------------------------------
    def _get_settings(self) -> Settings:
        if self._settings is not None:
            return self._settings
        p = Path(self._config_path) if self._config_path else Path("./config/settings.yaml")
        if p.is_file():
            self._settings = load_settings(str(p))
        else:
            self._settings = Settings()
        return self._settings

    def _document_service(self) -> Any:
        return self._services_resolved().document

    def _query_service(self) -> Any:
        return self._services_resolved().query

    def _services_resolved(self) -> Any:
        """Return the injected bundle or lazily build the app stack.

        NOTE: named ``_services_resolved`` (not ``_services``) so the cached
        ``self._services`` attribute cannot shadow it — a collision that made
        first real services access raise ``'NoneType' object is not callable``.
        """
        if self._injected_services is not None:
            return self._injected_services
        if self._services is None:
            with self._lock:
                if self._services is None:
                    from src.application.composition import build_application_services

                    self._services = build_application_services(
                        data_dir=self._data_dir,
                        config_path=self._config_path,
                    )
        return self._services

    # ------------------------------------------------------------------
    # list_collections
    # ------------------------------------------------------------------
    def _list_bm25(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        bm25_dir = Path(self._data_dir) / "db" / "bm25"
        if not bm25_dir.is_dir():
            return out
        for path in sorted(bm25_dir.glob("*.json")):
            name = path.stem
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to read bm25 index %s: %s", path, exc)
                continue
            chunks = 0
            if isinstance(payload, dict):
                n_docs = payload.get("n_docs")
                chunks = n_docs if isinstance(n_docs, int) else len(payload.get("docs") or [])
            out[name] = {"bm25_chunks": chunks, "data_dir": str(bm25_dir)}
        return out

    def _vector_counts(self, settings: Settings, names: list[str]) -> dict[str, Any]:
        """Best-effort per-collection vector counts; missing → ``None``."""
        out: dict[str, Any] = {}
        try:
            from src.libs.vector_store import VectorStoreFactory
            from src.libs.vector_store.chroma_store import chroma_collection_name

            router = VectorStoreFactory.create_multi_collection(settings.vector_store)
            existing: set[str] = set()
            client = getattr(router, "_client", None)
            if client is not None:
                try:
                    existing = {c.name for c in client.list_collections()}
                except Exception as exc:  # noqa: BLE001
                    logger.debug("chroma list_collections failed: %s", exc)
            for name in names:
                if chroma_collection_name(name) not in existing:
                    out[name] = None
                    continue
                try:
                    out[name] = int(router.count(collection=name))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("vector count unavailable for %s: %s", name, exc)
                    out[name] = None
        except Exception as exc:  # noqa: BLE001
            logger.debug("multi-collection vector store unavailable: %s", exc)
        return out

    def _document_counts(self, names: list[str]) -> dict[str, int | None]:
        """Best-effort distinct-document counts per collection.

        Uses the ingestion-history registry's per-collection count; a
        missing/unreadable DB yields ``None`` (never a fabricated 0).
        """
        out: dict[str, int | None] = {name: None for name in names}
        try:
            from src.libs.loader.file_integrity import SQLiteIntegrityChecker

            checker = SQLiteIntegrityChecker(
                str(Path(self._data_dir) / "db" / "ingestion_history.db"),
            )
            for name in names:
                try:
                    out[name] = int(checker.count(collection=name))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("document count unavailable for %s: %s", name, exc)
                    out[name] = None
        except Exception as exc:  # noqa: BLE001
            logger.debug("ingestion-history DB unavailable for doc counts: %s", exc)
        return out

    def list_collections(
        self, principal: AccessPrincipalLike,
    ) -> list[CollectionInfo]:
        settings = self._get_settings()
        bm25 = self._list_bm25()
        configured = settings.vector_store.collection_name
        names = sorted(set(bm25) | ({configured} if configured else set()))
        names = filter_accessible_collections(principal, names)
        counts = self._vector_counts(settings, names)
        document_counts = self._document_counts(names)

        chroma_dir = str(Path(self._data_dir) / "db" / "chroma")
        merged: dict[str, CollectionInfo] = {}
        for name in names:
            in_bm25 = name in bm25
            is_configured = name == configured
            if in_bm25 and is_configured:
                source = "both"
            elif in_bm25:
                source = "bm25"
            else:
                source = "vector_store"
            data_dir: str | None = chroma_dir
            bm25_chunks: int | None = None
            if in_bm25:
                bm25_chunks = bm25[name]["bm25_chunks"]
                data_dir = f"{bm25[name]['data_dir']} + {chroma_dir}"
            vector_count = counts.get(name)
            merged[name] = CollectionInfo(
                name=name,
                description=settings.mcp.collection_descriptions.get(name),
                document_count=document_counts.get(name),
                chunk_count=vector_count if vector_count is not None else bm25_chunks,
                source=source,
                bm25_chunks=bm25_chunks,
                vector_count=vector_count,
                data_dir=data_dir,
            )
        return sorted(merged.values(), key=lambda c: c.name)

    # ------------------------------------------------------------------
    # query_knowledge
    # ------------------------------------------------------------------
    def _build_search(self, *, collection: str, rerank: bool) -> tuple[Any, Any]:
        settings = self._get_settings()
        from src.ingestion.embedding.sparse_encoder import SparseEncoder
        from src.libs.embedding import EmbeddingFactory
        from src.libs.vector_store import VectorStoreFactory
        from src.libs.vector_store.scoped import ScopedCollectionVectorStore

        embedding = EmbeddingFactory.create(settings.embedding)
        router = VectorStoreFactory.create_multi_collection(settings.vector_store)
        vector_store = ScopedCollectionVectorStore(router, collection)
        sparse_encoder = SparseEncoder.from_settings(settings.sparse)

        from scripts.query import build_query_components

        hybrid = build_query_components(
            data_dir=self._data_dir,
            collection=collection,
            embedding=embedding,
            vector_store=vector_store,
            sparse_encoder=sparse_encoder,
        )
        from src.application.services import EmbeddingUsageStore, QueryService
        from src.application.services.trace_store import TraceStore
        from src.application.services.web_store import WebApiDB

        web_db = WebApiDB(Path(self._data_dir) / "db" / "web_api.db")
        usage_store = EmbeddingUsageStore(
            web_db, enabled=bool(getattr(embedding, "usage_supported", False)),
        )
        add_listener = getattr(embedding, "add_usage_listener", None)
        if add_listener is not None:
            add_listener(usage_store.record)
        query_service = QueryService(
            hybrid,
            trace_store=TraceStore(
                Path(self._data_dir) / "traces" / "traces.jsonl", db=web_db,
            ),
        )

        rerank_stage: Any = None
        if rerank and settings.rerank.backend != "none":
            from src.core.query_engine.reranker import RerankerStage
            from src.libs.reranker import RerankerFactory

            try:
                reranker = RerankerFactory.create(settings.rerank)
                rerank_stage = RerankerStage(
                    reranker=reranker, top_m=settings.rerank.top_m,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("reranker unavailable, degrading: %s", exc)
                rerank_stage = None
        return query_service, rerank_stage

    def query_knowledge(
        self, request: QueryRequest, principal: AccessPrincipalLike,
    ) -> KnowledgeQueryResult:
        from src.application.identifiers import document_uuid

        query_service, rerank_stage = self._build_search(
            collection=request.collection, rerank=request.rerank,
        )
        started = time.perf_counter()
        try:
            search_result = query_service.search(
                request.query, top_k=request.top_k,
                collection=request.collection,
            )
            candidates = search_result.chunks
            if rerank_stage is not None and candidates:
                results = rerank_stage.rerank(request.query, candidates).results
            else:
                results = candidates
        except Exception as exc:  # noqa: BLE001
            self._record_mcp_query(
                query=request.query, collection=request.collection, results=[],
                latency_ms=(time.perf_counter() - started) * 1000.0,
                degraded=True, error=type(exc).__name__, principal=principal,
            )
            raise UpstreamUnavailableError(
                f"knowledge retrieval failed: {type(exc).__name__}",
            ) from exc

        trace_id = getattr(search_result, "trace_id", None)
        degraded = bool(getattr(search_result, "degraded", False))
        reasons = list(getattr(search_result, "degraded_reasons", []) or [])
        if rerank_stage is None and request.rerank:
            degraded = True
            reasons.append("reranker unavailable; retrieval degraded")

        self._record_mcp_query(
            query=request.query, collection=request.collection, results=results,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            degraded=degraded, trace_id=trace_id, principal=principal,
        )

        evidence = [
            EvidenceItem(
                rank=i,
                chunk_id=r.chunk_id,
                document_id=str(document_uuid(
                    request.collection, r.metadata.get("source_path") or "",
                )),
                title=str(r.metadata.get("title") or ""),
                source=str(
                    r.metadata.get("source_path")
                    or r.metadata.get("source") or "(no source)",
                ),
                page=page_number_of(r.metadata),
                score=float(r.score if r.score is not None else 0.0),
                text=r.text or "",
                source_type=r.source or "fusion",
            )
            for i, r in enumerate(results, start=1)
        ]
        return KnowledgeQueryResult(
            query=request.query,
            collection=request.collection,
            count=len(evidence),
            evidence=evidence,
            diagnostics=Diagnostics(
                degraded=degraded, reasons=reasons, trace_id=trace_id,
            ),
        )

    def _record_mcp_query(
        self, *, query, collection, results, latency_ms, degraded,
        principal, trace_id=None, error=None,
    ) -> None:
        from src.application.identifiers import document_uuid
        from src.application.services.web_store import WebApiDB

        document_ids = sorted({
            str(document_uuid(collection, item.metadata.get("source_path", "")))
            for item in results if item.metadata.get("source_path")
        })
        try:
            WebApiDB(Path(self._data_dir) / "db" / "web_api.db").save_query_result(
                query_id=trace_id or str(uuid4()), collection=collection,
                query_text=query,
                result_json=json.dumps({
                    "chunks": [{} for _ in results],
                    "degraded": degraded,
                    "latency_ms": round(latency_ms, 1),
                    "error": error,
                }),
                document_ids=document_ids, source="mcp",
                api_key_id=getattr(principal, "key_id", None),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to record MCP query usage: %s", exc)

    # ------------------------------------------------------------------
    # get_document
    # ------------------------------------------------------------------
    def _resolve_doc(self, doc_id: str) -> tuple[str, str] | None:
        """Map a stable document UUID back to ``(collection, source_path)``."""
        from src.application.identifiers import document_uuid
        from src.libs.loader.file_integrity import SQLiteIntegrityChecker

        checker = SQLiteIntegrityChecker(
            str(Path(self._data_dir) / "db" / "ingestion_history.db"),
        )
        try:
            for rec in checker.list_processed(status="success"):
                if str(document_uuid(rec.collection, rec.file_path)) == str(doc_id):
                    return rec.collection, rec.file_path
        except Exception as exc:  # noqa: BLE001
            logger.warning("ingestion history read failed for %s: %s", doc_id, exc)
        return None

    def _read_document_chunks(self, collection: str, source_path: str) -> list[dict]:
        from src.libs.vector_store import VectorStoreFactory
        from src.libs.vector_store.scoped import ScopedCollectionVectorStore

        settings = self._get_settings()
        router = VectorStoreFactory.create_multi_collection(settings.vector_store)
        store = ScopedCollectionVectorStore(router, collection)
        return store.get_by_metadata({"source_path": source_path}, limit=10_000)

    def get_document(
        self, document_id: str, principal: AccessPrincipalLike,
    ) -> DocumentInfo:
        resolved = self._resolve_doc(document_id)
        if resolved is None:
            raise ResourceNotFoundError("document not found")
        collection, source_path = resolved
        try:
            require_collection_access(principal, collection)
        except CollectionAccessDenied as exc:
            raise AccessDeniedError("document not found or not accessible") from exc

        try:
            hits = self._read_document_chunks(collection, source_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vector store lookup failed for %s: %s", document_id, exc)
            hits = []
        if not hits:
            raise ResourceNotFoundError("document not found")

        meta = hits[0].get("metadata") or {}
        return DocumentInfo(
            document_id=document_id,
            collection=collection,
            title=str(meta.get("title") or "(untitled)"),
            document_type=str(meta.get("doc_type") or ""),
            source=str(meta.get("source_path") or source_path or ""),
            summary=str(meta.get("summary") or ""),
            tags=list(meta.get("tags") or []),
            chunk_count=int(len(hits)),
        )

    # ------------------------------------------------------------------
    # get_document_chunks — Phase 3 (stable ordering + pagination)
    # ------------------------------------------------------------------
    def _resolve_store_access(
        self, document_id: str, principal: AccessPrincipalLike,
    ) -> tuple[str, str]:
        """Resolve a document and return ``(collection, source_path)`` after
        authorization, raising the unified errors otherwise."""
        resolved = self._resolve_doc(document_id)
        if resolved is None:
            raise ResourceNotFoundError("document not found")
        collection, source_path = resolved
        try:
            require_collection_access(principal, collection)
        except CollectionAccessDenied as exc:
            raise AccessDeniedError("document not found or not accessible") from exc
        return collection, source_path

    @staticmethod
    def _chunk_sort_key(hit: dict[str, Any]) -> tuple[int, int, str]:
        """Stable ordering key — delegates to the shared helper (B1)."""
        return chunk_sort_key(hit)

    def _ordered_chunks(self, collection: str, source_path: str) -> list[dict]:
        """Read every chunk for a document and order it deterministically
        without relying on the vector store's natural order."""
        hits = self._read_document_chunks(collection, source_path)
        return stable_order_chunks(hits)

    def get_document_chunks(
        self,
        document_id: str,
        page: int,
        page_size: int,
        principal: AccessPrincipalLike,
    ) -> DocumentChunkPage:
        if page_size < 1 or page_size > 50:
            raise InvalidRequestError("page_size must be between 1 and 50")
        if page < 1:
            raise InvalidRequestError("page must be >= 1")

        collection, source_path = self._resolve_store_access(document_id, principal)
        try:
            ordered = self._ordered_chunks(collection, source_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vector store lookup failed for %s: %s", document_id, exc)
            ordered = []
        if not ordered:
            raise ResourceNotFoundError("document not found")

        total = len(ordered)
        start = (page - 1) * page_size
        window = ordered[start:start + page_size]
        chunks = [
            DocumentChunk(
                chunk_id=str(
                    (hit.get("id") or "")
                    or str((hit.get("metadata") or {}).get("chunk_id") or ""),
                ),
                index=start + i,
                text=(hit.get("text") or ""),
                page=page_number_of(hit.get("metadata") or {}),
                section=str((hit.get("metadata") or {}).get("section") or ""),
            )
            for i, hit in enumerate(window)
        ]
        return DocumentChunkPage(
            document_id=document_id,
            page=page,
            page_size=page_size,
            total=total,
            chunks=chunks,
        )


__all__ = ["InProcessRagReadOnlyClient"]