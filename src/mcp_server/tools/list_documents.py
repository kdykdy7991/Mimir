"""Bounded, scoped document discovery tool (Task 03)."""

from __future__ import annotations

from typing import Any

from src.mcp_server.auth.authorization import (
    CollectionAccessDenied,
    CollectionSelectionRequired,
    resolve_query_collection,
)
from src.mcp_server.auth.context import current_principal
from src.mcp_server.clients.errors import AccessDeniedError, InvalidRequestError, ResourceNotFoundError
from src.mcp_server.clients.models import DocumentListRequest
from src.mcp_server.presentation.budgets import active_budget
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error
from src.mcp_server.tools.common import client_from_args


def _input_schema() -> dict[str, Any]:
    budget = active_budget()
    return {
        "type": "object",
        "properties": {
            "collection": {"type": "string"},
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "page_size": {"type": "integer", "minimum": 1, "maximum": budget.page_size_max, "default": budget.page_size_default},
            "q": {"type": "string", "maxLength": 200},
            "status": {"type": "string", "enum": ["ready", "failed"]},
            "file_type": {"type": "string"},
            "folder_id": {"type": "string"},
            "tag_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "tag_operator": {"type": "string", "enum": ["and", "or"], "default": "and"},
            "updated_after": {"type": "number"},
            "updated_before": {"type": "number"},
            "sort": {"type": "string", "enum": ["updated_desc", "updated_asc", "name_asc", "name_desc", "size_desc"], "default": "updated_desc"},
        },
        "additionalProperties": False,
    }


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "collection": {"type": "string"}, "page": {"type": "integer"},
        "page_size": {"type": "integer"}, "total": {"type": "integer"},
        "has_next": {"type": "boolean"},
        "documents": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["collection", "page", "page_size", "total", "has_next", "documents"],
}


async def _list_documents(args: dict[str, Any]) -> Any:
    principal = current_principal()
    try:
        collection = resolve_query_collection(principal, args.get("collection"))
        page = int(args.get("page", 1))
        page_size = int(args.get("page_size", active_budget().page_size_default))
    except (CollectionAccessDenied, CollectionSelectionRequired):
        return tool_error("collection not found or not accessible")
    except (TypeError, ValueError):
        return tool_error("'page' and 'page_size' must be integers")
    request = DocumentListRequest(
        collection=collection, page=page, page_size=page_size,
        q=args.get("q"), status=args.get("status"), file_type=args.get("file_type"),
        folder_id=args.get("folder_id"), tag_ids=tuple(args.get("tag_ids") or ()),
        tag_operator=str(args.get("tag_operator", "and")),
        updated_after=args.get("updated_after"), updated_before=args.get("updated_before"),
        sort=str(args.get("sort", "updated_desc")),
    )
    try:
        result = client_from_args(args).list_documents(request, principal)
    except (ResourceNotFoundError, AccessDeniedError):
        return tool_error("collection not found or not accessible")
    except InvalidRequestError as exc:
        return tool_error(str(exc))
    documents = [
        {
            "document_id": d.document_id, "collection": d.collection,
            "title": d.title, "document_type": d.document_type,
            "source": d.source, "status": d.status,
            "chunk_count": d.chunk_count, "image_count": d.image_count,
            "tags": list(d.tags), "folder_id": d.folder_id,
            "created_at": d.created_at, "updated_at": d.updated_at,
        } for d in result.documents
    ]
    structured = {
        "collection": result.collection, "page": result.page,
        "page_size": result.page_size, "total": result.total,
        "has_next": result.has_next, "documents": documents,
    }
    lines = [f"# Documents in {result.collection}", f"Page {result.page} · {len(documents)} of {result.total}", ""]
    lines.extend(f"- `{d['document_id']}` — {d['title']} ({d['chunk_count']} chunks)" for d in documents)
    return "\n".join(lines), structured


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="list_documents",
        description="Discover authorized documents with bounded filters, stable sorting and pagination. Chunk bodies are never included.",
        input_schema=_input_schema(), handler=_list_documents,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]
