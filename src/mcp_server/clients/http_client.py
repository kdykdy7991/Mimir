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

import logging
import threading
from typing import Any

import httpx

from src.mcp_server.clients.base import RagReadOnlyClient
from src.mcp_server.clients.errors import (
    AccessDeniedError,
    InvalidRequestError,
    ResourceNotFoundError,
    UpstreamTimeoutError,
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

# ---------------------------------------------------------------------------
# Internal API response envelope: stable ``code`` + ``message`` (no stack).
# ---------------------------------------------------------------------------
_CODE_TO_CLIENT_ERROR: dict[str, type] = {
    "invalid_request": InvalidRequestError,
    "not_found": ResourceNotFoundError,
    "access_denied": AccessDeniedError,
    "upstream_unavailable": UpstreamUnavailableError,
    "upstream_timeout": UpstreamTimeoutError,
}
_STATUS_TO_CLIENT_ERROR: dict[int, type] = {
    400: InvalidRequestError,
    403: AccessDeniedError,
    404: ResourceNotFoundError,
}


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
            if code in _CODE_TO_CLIENT_ERROR:
                raise _CODE_TO_CLIENT_ERROR[code](message)
            status_cls = _STATUS_TO_CLIENT_ERROR.get(resp.status_code)
            if status_cls is not None:
                raise status_cls(message)
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
        return {"X-MCP-Allowed-Collections": ",".join(sorted(allowed))}

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

    def get_document(self, document_id: str, principal) -> DocumentInfo:
        data = self._get(
            f"/internal/mcp/v1/documents/{document_id}",
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
        )

    def get_document_chunks(
        self, document_id: str, page: int, page_size: int, principal,
    ) -> DocumentChunkPage:
        data = self._get(
            f"/internal/mcp/v1/documents/{document_id}/chunks",
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


__all__ = ["HttpRagReadOnlyClient"]