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
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.application.contracts import (
    AssetContent,
    AssetV1,
    ChunkContextRequest,
    ChunkContextResult,
    ContextChunkV1,
    ContextInclude,
    FailurePolicy,
    SearchDiagnostics,
    SearchRequest,
    SearchResult,
    WarningCode,
    WarningV1,
    rank_evidence,
    reciprocal_rank_fusion,
)

from src.core.settings import Settings, load_settings

from src.ingestion.chunk_order import (
    build_source_locator,
    chunk_id_of,
    chunk_sort_key,
    heading_of,
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
    ChunkDetail,
    Diagnostics,
    DocumentChunk,
    DocumentChunkPage,
    DocumentInfo,
    DocumentListRequest,
    DocumentPage,
    DocumentSummary,
    EvidenceItem,
    KnowledgeQueryResult,
    QueryRequest,
    DataSourceInfo,
    SyncStatusInfo,
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

    def _datasource_store(self):
        from src.connectors.store import DataSourceStore
        return DataSourceStore(Path(self._data_dir) / "db" / "data_sources.db")

    @staticmethod
    def _source_info(source) -> DataSourceInfo:
        return DataSourceInfo(
            id=source.id, name=source.name, connector_type=source.connector_type,
            collection_id=source.collection_id, enabled=source.enabled,
            checkpoint_revision=source.checkpoint_revision, updated_at=source.updated_at,
        )

    def list_data_sources(self, principal) -> list[DataSourceInfo]:
        allowed = getattr(principal, "allowed_collections", None)
        return [
            self._source_info(source) for source in self._datasource_store().list()
            if allowed is None or source.collection_id in allowed
        ]

    def get_sync_status(self, source_id: str, principal) -> SyncStatusInfo:
        try:
            value = self._datasource_store().sync_status(source_id)
        except KeyError as exc:
            raise ResourceNotFoundError("data source not found or not accessible") from exc
        try:
            require_collection_access(principal, value["source"].collection_id)
        except CollectionAccessDenied as exc:
            raise ResourceNotFoundError("data source not found or not accessible") from exc
        return SyncStatusInfo(self._source_info(value["source"]), value["last_run"])

    def list_sync_failures(self, source_id: str, limit: int, principal) -> list[dict[str, object]]:
        self.get_sync_status(source_id, principal)
        return self._datasource_store().list_failures(source_id, limit=limit)

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
        result = self.search(SearchRequest(
            query=request.query, collection=request.collection,
            top_k=request.top_k, rerank=request.rerank,
        ), principal)
        evidence = []
        for rank, item in enumerate(result.evidence, start=1):
            stages = (("rerank", item.scores.rerank), ("fusion", item.scores.fusion),
                      ("dense", item.scores.dense), ("sparse", item.scores.sparse))
            source_type, score = next((name, value) for name, value in stages if value is not None)
            evidence.append(EvidenceItem(
                rank=rank, chunk_id=item.chunk_id, document_id=item.document_id,
                title=item.title or "", source=item.source_name or "(no source)",
                page=item.source_locator.page, score=float(score),
                text=item.content or item.content_preview or "",
                source_type=source_type,
            ))
        return KnowledgeQueryResult(
            query=result.query, collection=result.collection,
            count=len(evidence),
            evidence=evidence,
            diagnostics=Diagnostics(
                degraded=result.diagnostics.degraded,
                reasons=[warning.message for warning in result.warnings],
                trace_id=result.diagnostics.trace_id,
            ),
        )

    def search(
        self, request: SearchRequest, principal: AccessPrincipalLike,
    ) -> SearchResult:
        try:
            for collection in request.collections:
                require_collection_access(principal, collection)
        except CollectionAccessDenied as exc:
            raise AccessDeniedError("collection not found or not accessible") from exc
        if len(request.collections) > 1:
            return self._search_collections(request, principal)
        collection = request.collections[0]
        query_service, rerank_stage = self._build_search(
            collection=collection, rerank=request.rerank,
        )
        from src.application.services.search_filters import GovernanceFilterResolver
        from src.application.services.search_service import UnifiedSearchService

        services = self._services_resolved()
        document_service = getattr(services, "document", None)
        unified = UnifiedSearchService(
            query_service,
            GovernanceFilterResolver(
                document_service, getattr(services, "db", None),
            ),
            rerank_stage=rerank_stage,
        )
        try:
            return unified.search(request)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, (InvalidRequestError, ResourceNotFoundError, AccessDeniedError)):
                raise
            raise UpstreamUnavailableError(
                f"knowledge retrieval failed: {type(exc).__name__}",
            ) from exc

    def _search_collections(
        self, request: SearchRequest, principal: AccessPrincipalLike,
    ) -> SearchResult:
        """Run fair per-collection retrieval, then fuse and rerank once."""
        from src.application.services.search_filters import GovernanceFilterResolver
        from src.application.services.search_service import UnifiedSearchService
        from src.core.types import ChunkRecord, RetrievalResult

        services = self._services_resolved()
        document_service = getattr(services, "document", None)
        rankings = []
        warnings: list[WarningV1] = []
        successful: list[str] = []
        failed: list[str] = []
        rerank_stage = None
        for collection in request.collections:
            try:
                query_service, stage = self._build_search(
                    collection=collection, rerank=request.rerank,
                )
                rerank_stage = rerank_stage or stage
                unified = UnifiedSearchService(
                    query_service,
                    GovernanceFilterResolver(
                        document_service, getattr(services, "db", None),
                    ),
                )
                result = unified.search(replace(
                    request, collection=collection, collection_ids=(),
                    rerank=False, threshold=None,
                ))
                rankings.append(result.evidence)
                warnings.extend(result.warnings)
                successful.append(collection)
            except Exception as exc:  # noqa: BLE001
                if request.failure_policy is FailurePolicy.FAIL_FAST:
                    raise UpstreamUnavailableError(
                        f"knowledge retrieval failed: {type(exc).__name__}",
                    ) from exc
                failed.append(collection)
                warnings.append(WarningV1(
                    code=WarningCode.PARTIAL_COLLECTION_FAILURE,
                    message="one authorized collection could not be searched",
                    detail={"collection": collection, "error": type(exc).__name__},
                ))

        if not successful:
            raise UpstreamUnavailableError(
                "knowledge retrieval failed for every requested collection",
            )

        fused = reciprocal_rank_fusion(rankings, query_order=request.queries)
        if request.rerank and rerank_stage is None:
            warnings.append(WarningV1(
                code=WarningCode.RERANK_DEGRADED,
                message="reranker unavailable; retrieval degraded",
            ))
        elif request.rerank and fused:
            originals = {
                (item.collection_id, item.document_id, item.chunk_id): item
                for item in fused
            }
            candidates = [RetrievalResult(
                chunk=ChunkRecord(
                    id=item.chunk_id,
                    text=item.content or item.content_preview or "",
                    metadata={
                        "collection_id": item.collection_id,
                        "document_id": item.document_id,
                    },
                ),
                score=item.scores.fusion or 0.0,
                source="fusion",
            ) for item in fused]
            output = rerank_stage.rerank(request.query, candidates)
            reranked = []
            for row in output.results:
                key = (
                    str(row.metadata["collection_id"]),
                    str(row.metadata["document_id"]),
                    str(row.chunk_id),
                )
                item = originals[key]
                scores = item.scores
                if row.source == "rerank":
                    scores = replace(scores, rerank=float(row.score))
                reranked.append(replace(item, scores=scores))
            fused = tuple(reranked)
            if getattr(output, "fallback", False):
                warnings.append(WarningV1(
                    code=WarningCode.RERANK_DEGRADED,
                    message="reranker unavailable; preserved fusion order",
                ))
        evidence = rank_evidence(
            fused, threshold=request.threshold, limit=request.top_k,
        )
        return SearchResult(
            query=request.query, collection=None,
            collections=request.collections, mode=request.mode,
            evidence=evidence, warnings=tuple(warnings),
            diagnostics=SearchDiagnostics(
                degraded=bool(warnings), executed_queries=request.queries,
                successful_collections=tuple(successful),
                failed_collections=tuple(failed),
                fused_candidates=sum(len(rows) for rows in rankings),
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
        if self._injected_services is not None:
            from uuid import UUID
            try:
                return self._document_service().resolve_document_id(UUID(str(doc_id)))
            except (ValueError, TypeError):
                return None
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
            raise ResourceNotFoundError("document not found or not accessible") from exc

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
            source=Path(str(meta.get("source_path") or source_path or "")).name,
            summary=str(meta.get("summary") or ""),
            tags=list(meta.get("tags") or []),
            chunk_count=int(len(hits)),
            parser=str(meta.get("parser") or "") or None,
            index_status=str(meta.get("index_status") or "ready"),
            content_type=str(meta.get("content_type") or "") or None,
            updated_at=(float(meta["updated_at"]) if meta.get("updated_at") is not None else None),
        )

    # ------------------------------------------------------------------
    # list_documents — bounded shared discovery service
    # ------------------------------------------------------------------
    def list_documents(
        self, request: DocumentListRequest, principal: AccessPrincipalLike,
    ) -> DocumentPage:
        from src.application.contracts import ContractError
        from src.application.identifiers import collection_uuid, document_uuid
        from src.mcp_server.presentation.budgets import active_budget

        try:
            require_collection_access(principal, request.collection)
        except CollectionAccessDenied as exc:
            raise ResourceNotFoundError("collection not found or not accessible") from exc
        budget = active_budget()
        try:
            budget.check_page(request.page)
            budget.check_page_size(request.page_size)
        except ContractError as exc:
            raise InvalidRequestError(str(exc)) from exc
        if request.tag_operator not in {"and", "or"}:
            raise InvalidRequestError("tag_operator must be 'and' or 'or'")
        if len((request.q or "").strip()) > 200:
            raise InvalidRequestError("q must be at most 200 characters")

        services = self._services_resolved()
        source_paths: list[str] | None = None
        db = getattr(services, "db", None)
        collection_id = str(collection_uuid(request.collection))
        if request.folder_id is not None:
            if db is None:
                source_paths = []
            else:
                folder = None if request.folder_id == "root" else request.folder_id
                doc_ids = db.documents_by_folder(folder, collection_id)
                source_paths = self._source_paths_for_ids(request.collection, doc_ids)
        if request.tag_ids:
            tagged_ids: list[str] = []
            if db is not None:
                if request.tag_operator == "and":
                    tagged_ids = db.documents_with_all_tags(list(request.tag_ids))
                else:
                    tag_map = db.document_tags_map(
                        [str(document_uuid(c, p)) for c, p in self._document_service().list_document_keys(collection=request.collection)],
                    )
                    wanted = set(request.tag_ids)
                    tagged_ids = [doc for doc, tags in tag_map.items() if wanted.intersection(tags)]
            tagged_paths = self._source_paths_for_ids(request.collection, tagged_ids)
            source_paths = tagged_paths if source_paths is None else [p for p in source_paths if p in set(tagged_paths)]

        status = {"ready": "success", "failed": "failed"}.get(request.status, request.status)
        try:
            infos, total = self._document_service().list_documents_paged(
                collection=request.collection,
                offset=(request.page - 1) * request.page_size,
                limit=request.page_size,
                status=status,
                q=(request.q or "").strip() or None,
                file_type=(request.file_type or "").lstrip(".") or None,
                updated_after=request.updated_after,
                updated_before=request.updated_before,
                sort=request.sort,
                source_paths_include=source_paths,
            )
        except Exception as exc:  # noqa: BLE001
            raise UpstreamUnavailableError(
                f"document discovery failed: {type(exc).__name__}",
            ) from exc

        doc_ids = [str(document_uuid(i.collection, i.source_path)) for i in infos]
        tags_by_doc = db.document_tags_map(doc_ids) if db is not None else {}
        folders = db.document_folder_map(doc_ids, collection_id) if db is not None else {}
        tag_names: dict[str, str] = {}
        if db is not None:
            tag_names = {str(t["tag_id"]): str(t["name"]) for t in db.list_tags(collection_id)}
        documents = []
        for info, doc_id in zip(infos, doc_ids):
            documents.append(DocumentSummary(
                document_id=doc_id,
                collection=info.collection,
                title=Path(info.source_path).name,
                document_type=Path(info.source_path).suffix.lstrip(".").lower(),
                source=Path(info.source_path).name,
                status={"success": "ready"}.get(info.status, info.status),
                chunk_count=int(info.n_chunks), image_count=int(info.n_images),
                tags=[tag_names.get(t, t) for t in tags_by_doc.get(doc_id, [])],
                folder_id=folders.get(doc_id), created_at=info.created_at,
                updated_at=info.updated_at or info.last_modified,
            ))
        return DocumentPage(
            collection=request.collection, page=request.page,
            page_size=request.page_size, total=total, documents=documents,
        )

    def _source_paths_for_ids(self, collection: str, doc_ids: list[str]) -> list[str]:
        wanted = set(doc_ids)
        from src.application.identifiers import document_uuid
        return [
            path for coll, path in self._document_service().list_document_keys(collection=collection)
            if str(document_uuid(coll, path)) in wanted
        ]

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
            raise ResourceNotFoundError("document not found or not accessible") from exc
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
        # Single source for pagination bounds (Task 02.3): the *active*
        # budget, i.e. the same object that built the tool's JSON Schema,
        # so a configured limit can never disagree with the declared one.
        # Unset (tests, web API embedding) → module defaults, which keep
        # the legacy messages byte-identical.
        from src.application.contracts import ContractError
        from src.mcp_server.presentation.budgets import active_budget

        budget = active_budget()
        try:
            budget.check_page(page)
            budget.check_page_size(page_size)
        except ContractError as exc:
            raise InvalidRequestError(str(exc)) from exc

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

    def get_chunk(
        self, document_id: str, chunk_id: str, principal: AccessPrincipalLike,
    ) -> ChunkDetail:
        collection, source_path = self._resolve_store_access(document_id, principal)
        try:
            ordered = self._ordered_chunks(collection, source_path)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamUnavailableError(
                f"chunk lookup failed: {type(exc).__name__}",
            ) from exc
        position = next(
            (i for i, hit in enumerate(ordered) if chunk_id_of(hit) == chunk_id),
            None,
        )
        if position is None:
            raise ResourceNotFoundError("chunk not found or not accessible")
        hit = ordered[position]
        meta = hit.get("metadata") or {}
        heading = heading_of(hit)
        content_type = str(meta.get("content_type") or "text")
        raw_assets = meta.get("asset_ids") or meta.get("image_ids") or []
        if isinstance(raw_assets, str):
            raw_assets = [x.strip() for x in raw_assets.split(",") if x.strip()]
        return ChunkDetail(
            document_id=document_id, chunk_id=chunk_id, index=position,
            text=str(hit.get("text") or ""), heading=heading,
            page=page_number_of(meta), content_type=content_type,
            previous_chunk_id=(chunk_id_of(ordered[position - 1]) if position else None),
            next_chunk_id=(chunk_id_of(ordered[position + 1]) if position + 1 < len(ordered) else None),
            parent_id=(
                str(meta.get("parent_chunk_id") or meta.get("parent_id"))
                if meta.get("parent_chunk_id") or meta.get("parent_id") else None
            ),
            source_locator=build_source_locator(
                meta, source_path=source_path, content_type=content_type,
                heading=heading,
            ),
            asset_ids=[str(x) for x in raw_assets],
            document_version=(str(meta["document_version"]) if meta.get("document_version") else None),
            chunk_version=(str(meta["chunk_version"]) if meta.get("chunk_version") else None),
            is_current=True,
        )

    def get_authorized_chunk_snapshot(
        self, document_id: str, chunk_id: str, principal: AccessPrincipalLike,
    ) -> Any:
        """Application adapter for revision history; not an MCP tool method."""
        from src.application.services.revision_service import AuthorizedChunkSnapshot

        collection, source_path = self._resolve_store_access(document_id, principal)
        try:
            ordered = self._ordered_chunks(collection, source_path)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamUnavailableError(
                f"chunk lookup failed: {type(exc).__name__}",
            ) from exc
        hit = next((row for row in ordered if chunk_id_of(row) == chunk_id), None)
        if hit is None:
            raise ResourceNotFoundError("chunk not found or not accessible")
        return AuthorizedChunkSnapshot(
            collection=collection, document_id=document_id, chunk_id=chunk_id,
            text=str(hit.get("text") or ""), metadata=dict(hit.get("metadata") or {}),
        )

    def get_revision_document_chunks(
        self, collection: str, document_id: str,
    ) -> list[Any]:
        """Return current document chunks for the trusted rebuild worker."""
        from src.core.types import Chunk

        resolved = self._resolve_doc(document_id)
        if resolved is None or resolved[0] != collection:
            raise ResourceNotFoundError("document not found or not accessible")
        _, source_path = resolved
        return [
            Chunk(
                id=chunk_id_of(hit), text=str(hit.get("text") or ""),
                metadata=dict(hit.get("metadata") or {}),
                start_offset=int((hit.get("metadata") or {}).get("start_offset") or 0),
                end_offset=int((hit.get("metadata") or {}).get("end_offset") or 0),
                source_ref=document_id,
            )
            for hit in self._ordered_chunks(collection, source_path)
        ]

    def get_chunk_context(
        self, request: ChunkContextRequest, principal: AccessPrincipalLike,
    ) -> ChunkContextResult:
        collection, source_path = self._resolve_store_access(
            request.document_id, principal,
        )
        try:
            ordered = self._ordered_chunks(collection, source_path)
        except Exception as exc:  # noqa: BLE001
            raise UpstreamUnavailableError(
                f"chunk context lookup failed: {type(exc).__name__}",
            ) from exc
        position = next(
            (i for i, hit in enumerate(ordered) if chunk_id_of(hit) == request.chunk_id),
            None,
        )
        if position is None:
            raise ResourceNotFoundError("chunk not found or not accessible")

        def context_row(hit: dict[str, Any], relation: str) -> ContextChunkV1:
            meta = hit.get("metadata") or {}
            heading = heading_of(hit)
            content_type = str(meta.get("content_type") or "text")
            return ContextChunkV1(
                chunk_id=chunk_id_of(hit), relation=relation,
                text=str(hit.get("text") or ""), content_type=content_type,
                source_locator=build_source_locator(
                    meta, source_path=source_path, content_type=content_type,
                    heading=heading,
                ),
            )

        hit_row = context_row(ordered[position], "hit")
        parent_row = None
        if request.include in {ContextInclude.PARENT, ContextInclude.BOTH}:
            from src.ingestion.storage import ParentChunkStore

            parent_db = Path(self._data_dir) / "db" / "parent_chunks.db"
            stored = (
                ParentChunkStore(parent_db).parent_for_child(
                    collection, request.document_id, request.chunk_id,
                )
                if parent_db.is_file() else None
            )
            if stored is not None:
                parent_row = ContextChunkV1(
                    chunk_id=stored.chunk_id, relation="parent", text=stored.text,
                    content_type=str(stored.metadata.get("content_type") or "text"),
                    source_locator={
                        "kind": "parent",
                        "heading_path": stored.metadata.get("heading_path") or [],
                        "source_span": stored.metadata.get("source_span"),
                    },
                )
        neighbors: list[ContextChunkV1] = []
        if request.include in {ContextInclude.NEIGHBORS, ContextInclude.BOTH}:
            start = max(0, position - request.before)
            end = min(len(ordered), position + request.after + 1)
            for index in range(start, end):
                if index == position:
                    continue
                relation = "before" if index < position else "after"
                neighbors.append(context_row(ordered[index], relation))

        remaining = request.max_chars
        truncated = False

        def bounded(row: ContextChunkV1 | None) -> ContextChunkV1 | None:
            nonlocal remaining, truncated
            if row is None:
                return None
            text = row.text[:remaining]
            if len(text) < len(row.text):
                truncated = True
            remaining -= len(text)
            return replace(row, text=text)

        hit_row = bounded(hit_row)
        parent_row = bounded(parent_row)
        bounded_neighbors = []
        for row in neighbors:
            if remaining <= 0:
                truncated = True
                break
            bounded_neighbors.append(bounded(row))
        if len(bounded_neighbors) < len(neighbors):
            truncated = True
        return ChunkContextResult(
            document_id=request.document_id, hit=hit_row,
            parent=parent_row,
            neighbors=tuple(row for row in bounded_neighbors if row is not None),
            truncated=truncated,
        )

    def get_asset(
        self, document_id: str, asset_id: str, principal: AccessPrincipalLike,
    ) -> AssetContent:
        import hashlib
        import mimetypes

        from src.ingestion.storage import ImageStorage

        collection, source_path = self._resolve_store_access(document_id, principal)
        ordered = self._ordered_chunks(collection, source_path)
        owner = None
        document_version = None
        for hit in ordered:
            meta = hit.get("metadata") or {}
            assets = meta.get("asset_ids") or meta.get("image_ids") or meta.get("image_refs") or ()
            if isinstance(assets, str):
                assets = [x.strip() for x in assets.split(",") if x.strip()]
            if asset_id in {str(x) for x in assets}:
                owner = chunk_id_of(hit)
                document_version = str(meta.get("document_version") or "legacy-v1")
                break
        if owner is None:
            raise ResourceNotFoundError("asset not found or not accessible")
        image_db = Path(self._data_dir) / "db" / "image_index.db"
        if not image_db.is_file():
            raise ResourceNotFoundError("asset not found or not accessible")
        storage = ImageStorage(
            db_path=str(image_db),
            base_dir=str(Path(self._data_dir) / "images"),
        )
        record = storage.get_readonly(asset_id)
        if record is None or record.collection not in {collection, None}:
            raise ResourceNotFoundError("asset not found or not accessible")
        root = (Path(self._data_dir) / "images").resolve()
        path = Path(record.file_path).resolve()
        if root not in path.parents:
            raise ResourceNotFoundError("asset not found or not accessible")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ResourceNotFoundError("asset not found or not accessible") from exc
        if size > 10 * 1024 * 1024:
            raise InvalidRequestError("asset exceeds the 10485760-byte limit")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise InvalidRequestError("asset MIME type is not allowed")
        data = path.read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        locator = path.relative_to(root).as_posix()
        return AssetContent(
            metadata=AssetV1(
                asset_id=asset_id, document_id=document_id, chunk_id=owner,
                document_version=document_version, mime_type=mime,
                byte_size=len(data), checksum_sha256=checksum, locator=locator,
            ),
            data=data,
        )


__all__ = ["InProcessRagReadOnlyClient"]
