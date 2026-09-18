"""Unified dense/sparse/hybrid chunk search tool (Task 04)."""

from __future__ import annotations

from typing import Any

from src.application.contracts import (
    ContractError,
    EvidenceFilterV1,
    SearchRequest,
    to_jsonable,
)
from src.mcp_server.auth.authorization import (
    CollectionAccessDenied,
    CollectionSelectionRequired,
    require_collection_access,
    resolve_query_collection,
)
from src.mcp_server.auth.context import current_principal
from src.mcp_server.clients.errors import AccessDeniedError, InvalidRequestError, ResourceNotFoundError
from src.mcp_server.presentation.budgets import active_budget
from src.mcp_server.presentation.evidence_mapper import bound_evidence_page
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error
from src.mcp_server.tools.common import client_from_args


def _input_schema() -> dict[str, Any]:
    budget = active_budget()
    string_list = {"type": "array", "items": {"type": "string"}, "maxItems": 20}
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": budget.query_max_length},
            "collection": {"type": "string"},
            "collection_ids": {"type": "array", "items": {"type": "string"}, "maxItems": budget.collection_max_count},
            "alternate_queries": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": budget.query_max_length},
                "maxItems": budget.alternate_query_max_count,
            },
            "failure_policy": {
                "type": "string", "enum": ["fail_fast", "allow_partial"],
                "default": "fail_fast",
            },
            "mode": {"type": "string", "enum": ["dense", "sparse", "hybrid"], "default": "hybrid"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": budget.top_k_max, "default": budget.top_k_default},
            "rerank": {"type": "boolean", "default": True},
            "threshold": {"type": ["number", "null"]},
            "include_content": {"type": "boolean", "default": True},
            "filters": {
                "type": "object",
                "properties": {
                    "document_ids": string_list, "tag_ids": string_list,
                    "tag_operator": {"type": "string", "enum": ["and", "or"], "default": "and"},
                    "folder_id": {"type": "string"},
                    "include_descendants": {"type": "boolean", "default": False},
                    "file_types": string_list, "content_types": string_list,
                    "source_types": string_list,
                    "updated_after": {"type": "string", "format": "date-time"},
                    "updated_before": {"type": "string", "format": "date-time"},
                },
                "additionalProperties": False,
            },
        },
        "required": ["query"], "additionalProperties": False,
    }


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"}, "collection": {"type": ["string", "null"]},
        "collections": {"type": "array", "items": {"type": "string"}},
        "mode": {"type": "string"}, "count": {"type": "integer"},
        "evidence": {"type": "array", "items": {"type": "object"}},
        "warnings": {"type": "array", "items": {"type": "object"}},
        "diagnostics": {"type": "object"}, "truncated": {"type": "boolean"},
    },
    "required": ["query", "collection", "collections", "mode", "count", "evidence", "warnings", "diagnostics", "truncated"],
}


async def _search_chunks(args: dict[str, Any]) -> Any:
    principal = current_principal()
    try:
        requested_collections = args.get("collection_ids") or ()
        if args.get("collection") and requested_collections:
            raise ContractError("collection and collection_ids are mutually exclusive")
        if requested_collections:
            for item in requested_collections:
                require_collection_access(principal, item)
            collection = None
        else:
            collection = resolve_query_collection(principal, args.get("collection"))
        filters = EvidenceFilterV1.from_mapping(args["filters"]) if args.get("filters") else None
        request = SearchRequest(
            query=args.get("query", ""), collection=collection,
            alternate_queries=tuple(args.get("alternate_queries") or ()),
            collection_ids=tuple(requested_collections),
            failure_policy=args.get("failure_policy", "fail_fast"),
            mode=args.get("mode", "hybrid"), filters=filters,
            top_k=args.get("top_k", active_budget().top_k_default),
            rerank=args.get("rerank", True), threshold=args.get("threshold"),
            include_content=args.get("include_content", True),
            response_budget=active_budget(),
        )
    except (CollectionAccessDenied, CollectionSelectionRequired):
        return tool_error("collection not found or not accessible")
    except (ContractError, TypeError, ValueError) as exc:
        return tool_error(str(exc))
    try:
        result = client_from_args(args).search(request, principal)
    except (AccessDeniedError, ResourceNotFoundError):
        return tool_error("collection not found or not accessible")
    except InvalidRequestError as exc:
        return tool_error(str(exc))

    page = bound_evidence_page(result.evidence, active_budget())
    warnings = tuple(result.warnings) + tuple(page.warnings)
    structured = {
        "query": result.query, "collection": result.collection,
        "collections": list(result.collections),
        "mode": result.mode.value, "count": len(page.results),
        "evidence": [to_jsonable(item) for item in page.results],
        "warnings": [to_jsonable(item) for item in warnings],
        "diagnostics": to_jsonable(result.diagnostics),
        "truncated": bool(result.truncated or page.truncated_results or page.truncated_characters),
    }
    scope = ", ".join(result.collections)
    lines = [f"# Search evidence ({result.mode.value})", f"{len(page.results)} result(s) from `{scope}`", ""]
    for index, item in enumerate(page.results, start=1):
        preview = item.content or item.content_preview or "(content omitted)"
        lines.append(f"## [{index}] `{item.chunk_id}`\n\n{preview}")
    return "\n\n".join(lines), structured


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="search_chunks",
        description="Search one authorized collection using dense, sparse, or hybrid retrieval with governance filters. Returns evidence, never an answer.",
        input_schema=_input_schema(), handler=_search_chunks,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]
