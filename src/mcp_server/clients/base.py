"""
Read-only client interface (P1.1).

``RagReadOnlyClient`` is the *only* route from the MCP tool handlers to
the RAG stack. The interface carries no fastMCP/MCP content types, no
Chroma records, no Web DTOs and no LLM types — only the pure models in
``clients/models.py`` and the auth principal.

Two implementations live behind it:

- :class:`InProcessRagReadOnlyClient` (migration default) — reuses the
  running application services;
- :class:`HttpRagReadOnlyClient` (independent deployment) — talks to the
  main service's read-only HTTP API.

Tool handlers must not construct Embedding / VectorStore / SQLite /
Chroma themselves; they call this interface (plan §6, §11).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.mcp_server.auth.context import AccessPrincipalLike
from src.mcp_server.clients.models import (
    CollectionInfo,
    DocumentChunkPage,
    DocumentInfo,
    KnowledgeQueryResult,
    QueryRequest,
)


@runtime_checkable
class RagReadOnlyClient(Protocol):
    """Minimal read-only surface MCP needs.

    Every method is read-only: none of them can create, upload, update or
    delete knowledge. This is the boundary that makes "no store mutation
    from a read tool" structurally enforced once handlers only reach the
    store through this interface.
    """

    def list_collections(
        self, principal: AccessPrincipalLike,
    ) -> list[CollectionInfo]:
        """Return the collections authorized for ``principal``."""
        ...

    def query_knowledge(
        self, request: QueryRequest, principal: AccessPrincipalLike,
    ) -> KnowledgeQueryResult:
        """Return raw retrieval evidence for ``request.query`` (no answer)."""
        ...

    def get_document(
        self, document_id: str, principal: AccessPrincipalLike,
    ) -> DocumentInfo:
        """Return read-only metadata for ``document_id``."""
        ...

    def get_document_chunks(
        self,
        document_id: str,
        page: int,
        page_size: int,
        principal: AccessPrincipalLike,
    ) -> DocumentChunkPage:
        """Return one stable-ordered page of ``document_id``'s chunks."""
        ...


__all__ = ["RagReadOnlyClient"]