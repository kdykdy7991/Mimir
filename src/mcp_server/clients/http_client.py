"""
``HttpRagReadOnlyClient`` — read-only client over the main service's
internal HTTP API (Phase 4, §P4.2/§4 WeKnora borrow).

This Client mimics WeKnora's ``WeKnoraClient`` (finetune open-source
``weknora_mcp_server.py``: base URL + thread-local reused Session +
credentials carried on a request header). It is a deliberate, narrowed
re-implementation: only the four read operations are exposed, the http
Session is bounded and reused per thread, TLS is verified by default, and
the credential header is never logged.

Attribution / provenance:
    - File-header source attribution + commit subject `adapted from
      WeKnora` are required for the WeKnoraClient borrow (see plan §4).

Only a small, fixed set of methods exists — there is deliberately NO
generic ``request(method, path, ...)`` escape hatch, so a future Tool
cannot bypass the read-only boundary through this client.
"""

from __future__ import annotations

import base64
import logging
import threading
from typing import Any
from urllib.parse import quote

import httpx

from src.application.contracts import (
    ChunkContextRequest,
    ChunkContextResult,
    ContextChunkV1,
    AssetContent,
    AssetV1,
    EvidenceV1,
    SearchDiagnostics,
    SearchMode,
    SearchRequest,
    SearchResult,
    WarningV1,
)

from src.mcp_server.clients.base import RagReadOnlyClient
from src.mcp_server.clients.errors import (
    AccessDeniedError,
    InvalidRequestError,
    OverloadedError,
    RateLimitedError,
    ResourceNotFoundError,
    UpstreamTimeoutError,
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

# ---------------------------------------------------------------------------
# Internal API response envelope: stable ``code`` + ``message`` (no stack).
# ---------------------------------------------------------------------------
_CODE_TO_CLIENT_ERROR: dict[str, type] = {
    "invalid_request": InvalidRequestError,
    "not_found": ResourceNotFoundError,
    "access_denied": AccessDeniedError,
    "upstream_unavailable": UpstreamUnavailableError,
    "upstream_timeout": UpstreamTimeoutError,
    # Task 02.3 stable error plane — types only, no limiter implemented.
    "rate_limited": RateLimitedError,
    "overloaded": OverloadedError,
}
_STATUS_TO_CLIENT_ERROR: dict[int, type] = {
    400: InvalidRequestError,
    403: AccessDeniedError,
    404: ResourceNotFoundError,
    # 429 is a budget rejection, never an upstream outage. A bare 5xx
    # (incl. 503) stays upstream_unavailable; ``overloaded`` is only
    # raised when the upstream explicitly sends that stable code.
    429: RateLimitedError,
}


def _retry_after_seconds(resp: "httpx.Response") -> float | None:
    """Parse a Retry-After delta-seconds header (HTTP dates ignored)."""
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class HttpRagReadOnlyClient(RagReadOnlyClient):
    """Thread-local, bounded, read-only HTTP client for the internal API."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout_s: float = 30.0,
        max_connections: int = 10,
        max_keepalive: int = 5,
        verify_tls: bool = True,
        transport: httpx.BaseTransport | None = None,
        trust_env: bool = True,
    ) -> None:
        if not base_url:
            raise ValueError("HttpRagReadOnlyClient requires a non-empty base_url")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key  # stored, never logged
        # Explicit connect/read/write/pool timeouts (Phase 4 §P4.2).
        self._timeout = httpx.Timeout(
            connect=min(timeout_s, 5.0),
            read=timeout_s,
            write=min(timeout_s, 10.0),
            pool=min(timeout_s, 5.0),
        )
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
        )
        self._verify_tls = verify_tls
        self._transport = transport
        self._trust_env = trust_env
        # Per-instance, per-thread reused Session (WeKnora borrow).
        self._local = threading.local()

    # ------------------------------------------------------------------
    # Reused per-thread bounded Session (WeKnora borrow, see header).
    # ------------------------------------------------------------------
    @property
    def _session(self) -> httpx.Client:
        session = getattr(self._local, "session", None)
        if session is None:
            headers: dict[str, str] = {}
            if self._api_key:
                headers["X-API-Key"] = self._api_key
            session = httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                limits=self._limits,
                verify=self._verify_tls,
                headers=headers,
                transport=self._transport,
                trust_env=self._trust_env,
            )
            self._local.session = session
        return session

    def close(self) -> None:
        session = getattr(self._local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
            self._local.session = None

    # ------------------------------------------------------------------
    # Internal plumbing — no generic request() surface for Tool callers.
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, **kw: Any) -> Any:
        """Low-level HTTP call + error translation.

        NOTE: this private helper is for the four read methods only; it is
        never exposed on the public read interface.
        """
        headers = kw.get("headers")
        if headers is None:
            headers = {}
            kw["headers"] = headers
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        try:
            resp = self._session.request(method, path, **kw)
        except httpx.TimeoutException:
            raise UpstreamTimeoutError(f"upstream timed out on {path}") from None
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError(
                f"upstream unavailable on {path}: {exc}",
            ) from exc

        try:
            payload = resp.json() if resp.content else {}
        except ValueError:
            payload = {}

        if resp.status_code >= 400:
            code = str((payload.get("code") or "") or "")
            message = str(payload.get("message") or f"HTTP {resp.status_code}")
            error_cls = _CODE_TO_CLIENT_ERROR.get(code)
            if error_cls is None:
                error_cls = _STATUS_TO_CLIENT_ERROR.get(resp.status_code)
            if error_cls is not None:
                if error_cls is RateLimitedError:
                    raise RateLimitedError(
                        message,
                        retry_after_seconds=_retry_after_seconds(resp),
                    )
                raise error_cls(message)
            if resp.status_code in (408, 504):
                raise UpstreamTimeoutError(message)
            raise UpstreamUnavailableError(message)
        return payload

    @staticmethod
    def _scope_headers(principal) -> dict[str, str] | None:
        """Forward only the caller's explicit collection grants.

        A ``TrustedLocalPrincipal`` (or empty grants) sends NO scope header,
        so the internal API can never be coerced into treating a remote
        caller as all-collections trusted.
        """
        allowed = getattr(principal, "allowed_collections", None)
        if not allowed:
            return None
        from src.mcp_server.clients.scope import encode_scope_header
        return {"X-MCP-Allowed-Collections": encode_scope_header(allowed)}

    def _get(
        self, path: str, params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._request("GET", path, params=params, headers=headers)

    def _post(
        self, path: str, json: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._request("POST", path, json=json, headers=headers)

    # ------------------------------------------------------------------
    # The four read operations.
    # ------------------------------------------------------------------
    def list_collections(self, principal) -> list[CollectionInfo]:
        data = self._get(
            "/internal/mcp/v1/collections",
            headers=self._scope_headers(principal),
        )
        return [
            CollectionInfo(
                name=str(item.get("name")),
                description=item.get("description"),
                document_count=item.get("document_count"),
                chunk_count=item.get("chunk_count"),
            )
            for item in data.get("collections", [])
        ]

    @staticmethod
    def _source_info(item: dict[str, Any]) -> DataSourceInfo:
        return DataSourceInfo(
            id=str(item["id"]), name=str(item["name"]),
            connector_type=str(item["connector_type"]),
            collection_id=str(item["collection_id"]), enabled=bool(item["enabled"]),
            checkpoint_revision=int(item.get("checkpoint_revision", 0)),
            updated_at=float(item["updated_at"]),
        )

    def list_data_sources(self, principal) -> list[DataSourceInfo]:
        data = self._get("/internal/mcp/v1/data-sources", headers=self._scope_headers(principal))
        return [self._source_info(item) for item in data.get("data_sources", [])]

    def get_sync_status(self, source_id: str, principal) -> SyncStatusInfo:
        data = self._get(
            f"/internal/mcp/v1/data-sources/{quote(source_id, safe='')}/status",
            headers=self._scope_headers(principal),
        )
        return SyncStatusInfo(self._source_info(data["data_source"]), data.get("last_run"))

    def list_sync_failures(self, source_id: str, limit: int, principal) -> list[dict[str, object]]:
        data = self._get(
            f"/internal/mcp/v1/data-sources/{quote(source_id, safe='')}/failures",
            params={"limit": limit}, headers=self._scope_headers(principal),
        )
        return list(data.get("failures", []))

    def query_knowledge(
        self, request: QueryRequest, principal,
    ) -> KnowledgeQueryResult:
        data = self._post("/internal/mcp/v1/query", {
            "query": request.query,
            "collection": request.collection,
            "top_k": request.top_k,
            "rerank": request.rerank,
        }, headers=self._scope_headers(principal))
        diag = data.get("diagnostics") or {}
        evidence = []
        for item in data.get("evidence", []):
            evidence.append(EvidenceItem(
                rank=int(item.get("rank", 0)),
                chunk_id=str(item.get("chunk_id", "")),
                document_id=str(item.get("document_id", "")),
                title=str(item.get("title", "")),
                source=str(item.get("source", "")),
                page=item.get("page"),
                score=float(item.get("score", 0.0)),
                text=str(item.get("text", "")),
            ))
        return KnowledgeQueryResult(
            query=data.get("query", request.query),
            collection=data.get("collection", request.collection),
            count=int(data.get("count", len(evidence))),
            evidence=evidence,
            diagnostics=Diagnostics(
                degraded=bool(diag.get("degraded", False)),
                reasons=[str(r) for r in diag.get("reasons", [])],
                trace_id=diag.get("trace_id"),
            ),
        )

    def search(self, request: SearchRequest, principal) -> SearchResult:
        payload: dict[str, Any] = {
            "query": request.query, "collection": request.collection,
            "alternate_queries": list(request.alternate_queries),
            "collection_ids": list(request.collection_ids),
            "failure_policy": request.failure_policy.value,
            "mode": request.mode.value, "top_k": request.top_k,
            "rerank": request.rerank, "threshold": request.threshold,
            "include_content": request.include_content,
        }
        if request.filters is not None:
            payload["filters"] = request.filters.to_dict()
        data = self._post(
            "/internal/mcp/v1/search", payload,
            headers=self._scope_headers(principal),
        )
        diag = data.get("diagnostics") or {}
        return SearchResult(
            query=str(data.get("query", request.query)),
            collection=(str(data["collection"]) if data.get("collection") else None),
            collections=tuple(data.get("collections") or request.collections),
            mode=SearchMode.coerce(data.get("mode", request.mode.value)),
            evidence=tuple(EvidenceV1.from_mapping(e) for e in data.get("evidence", [])),
            warnings=tuple(WarningV1.from_mapping(w) for w in data.get("warnings", [])),
            diagnostics=SearchDiagnostics(
                trace_id=diag.get("trace_id"), degraded=bool(diag.get("degraded", False)),
                dense_candidates=diag.get("dense_candidates"),
                sparse_candidates=diag.get("sparse_candidates"),
                fused_candidates=diag.get("fused_candidates"),
                executed_queries=tuple(diag.get("executed_queries") or ()),
                successful_collections=tuple(diag.get("successful_collections") or ()),
                failed_collections=tuple(diag.get("failed_collections") or ()),
            ),
            truncated=bool(data.get("truncated", False)),
        )

    def get_chunk_context(self, request: ChunkContextRequest, principal) -> ChunkContextResult:
        document_id = quote(request.document_id, safe="")
        chunk_id = quote(request.chunk_id, safe="")
        data = self._post(
            f"/internal/mcp/v1/documents/{document_id}/chunks/{chunk_id}/context",
            {
                "include": request.include.value, "before": request.before,
                "after": request.after, "max_chars": request.max_chars,
            },
            headers=self._scope_headers(principal),
        )

        def row(value: dict[str, Any]) -> ContextChunkV1:
            return ContextChunkV1(
                chunk_id=str(value.get("chunk_id", "")),
                relation=str(value.get("relation", "")),
                text=str(value.get("text", "")),
                content_type=str(value.get("content_type", "text")),
                source_locator=value.get("source_locator"),
            )

        return ChunkContextResult(
            document_id=str(data.get("document_id", request.document_id)),
            hit=row(data["hit"]),
            parent=row(data["parent"]) if data.get("parent") else None,
            neighbors=tuple(row(item) for item in data.get("neighbors", [])),
            truncated=bool(data.get("truncated", False)),
        )

    def get_asset(self, document_id: str, asset_id: str, principal) -> AssetContent:
        encoded_document = quote(document_id, safe="")
        encoded_asset = quote(asset_id, safe="")
        data = self._get(
            f"/internal/mcp/v1/documents/{encoded_document}/assets/{encoded_asset}",
            headers=self._scope_headers(principal),
        )
        try:
            raw = base64.b64decode(data.get("data_base64", ""), validate=True)
        except Exception as exc:
            raise UpstreamUnavailableError("invalid asset payload") from exc
        return AssetContent(
            metadata=AssetV1(**data["metadata"]),
            data=raw,
        )

    def get_document(self, document_id: str, principal) -> DocumentInfo:
        encoded_document_id = quote(document_id, safe="")
        data = self._get(
            f"/internal/mcp/v1/documents/{encoded_document_id}",
            headers=self._scope_headers(principal),
        )
        return DocumentInfo(
            document_id=data.get("document_id", document_id),
            collection=data.get("collection", ""),
            title=data.get("title", "(untitled)"),
            document_type=data.get("document_type", ""),
            source=data.get("source", ""),
            summary=data.get("summary", ""),
            tags=list(data.get("tags", [])),
            chunk_count=int(data.get("chunk_count", 0)),
            version=data.get("version"), folder_id=data.get("folder_id"),
            parser=data.get("parser"), index_status=data.get("index_status"),
            content_type=data.get("content_type"), updated_at=data.get("updated_at"),
        )

    def get_document_chunks(
        self, document_id: str, page: int, page_size: int, principal,
    ) -> DocumentChunkPage:
        encoded_document_id = quote(document_id, safe="")
        data = self._get(
            f"/internal/mcp/v1/documents/{encoded_document_id}/chunks",
            {"page": int(page), "page_size": int(page_size)},
            headers=self._scope_headers(principal),
        )
        return DocumentChunkPage(
            document_id=data.get("document_id", document_id),
            page=int(data.get("page", page)),
            page_size=int(data.get("page_size", page_size)),
            total=int(data.get("total", 0)),
            chunks=[
                DocumentChunk(
                    chunk_id=str(c.get("chunk_id", "")),
                    index=int(c.get("index", 0)),
                    text=str(c.get("text", "")),
                    page=c.get("page") if c.get("page") is not None else None,
                    section=str(c.get("section", "")),
                )
                for c in data.get("chunks", [])
            ],
        )

    def list_documents(self, request: DocumentListRequest, principal) -> DocumentPage:
        params: dict[str, Any] = {
            "collection": request.collection, "page": request.page,
            "page_size": request.page_size, "sort": request.sort,
            "tag_operator": request.tag_operator,
        }
        for key in ("q", "status", "file_type", "folder_id",
                    "updated_after", "updated_before"):
            value = getattr(request, key)
            if value is not None:
                params[key] = value
        if request.tag_ids:
            params["tag_id"] = list(request.tag_ids)
        data = self._get(
            "/internal/mcp/v1/documents", params,
            headers=self._scope_headers(principal),
        )
        return DocumentPage(
            collection=str(data.get("collection", request.collection)),
            page=int(data.get("page", request.page)),
            page_size=int(data.get("page_size", request.page_size)),
            total=int(data.get("total", 0)),
            documents=[DocumentSummary(
                document_id=str(d.get("document_id", "")),
                collection=str(d.get("collection", request.collection)),
                title=str(d.get("title", "")),
                document_type=str(d.get("document_type", "")),
                source=str(d.get("source", "")), status=str(d.get("status", "")),
                chunk_count=int(d.get("chunk_count", 0)),
                image_count=int(d.get("image_count", 0)),
                tags=[str(t) for t in d.get("tags", [])],
                folder_id=d.get("folder_id"), created_at=d.get("created_at"),
                updated_at=d.get("updated_at"),
            ) for d in data.get("documents", [])],
        )

    def get_chunk(self, document_id: str, chunk_id: str, principal) -> ChunkDetail:
        encoded_document_id = quote(document_id, safe="")
        encoded_chunk_id = quote(chunk_id, safe="")
        data = self._get(
            f"/internal/mcp/v1/documents/{encoded_document_id}/chunks/{encoded_chunk_id}",
            headers=self._scope_headers(principal),
        )
        return ChunkDetail(
            document_id=str(data.get("document_id", document_id)),
            chunk_id=str(data.get("chunk_id", chunk_id)),
            index=int(data.get("index", 0)), text=str(data.get("text", "")),
            heading=data.get("heading"), page=data.get("page"),
            content_type=str(data.get("content_type", "text")),
            previous_chunk_id=data.get("previous_chunk_id"),
            next_chunk_id=data.get("next_chunk_id"), parent_id=data.get("parent_id"),
            source_locator=dict(data.get("source_locator") or {}),
            asset_ids=[str(x) for x in data.get("asset_ids", [])],
            document_version=data.get("document_version"),
            chunk_version=data.get("chunk_version"),
            is_current=bool(data.get("is_current", True)),
        )


__all__ = ["HttpRagReadOnlyClient"]
