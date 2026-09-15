"""
Machine-readable server capabilities discovery (Task 02.4).

Exposes a single read-only MCP Resource — ``rag://server/capabilities``
— describing the contract version, the **actually registered** tools,
retrieval modes/rerank as configured, the evidence/filter/warning/error
vocabulary, the live response budget, and the capabilities the server
does not implement yet.

Fact-source discipline
-----------------------
* the tool list and structured-output support come from the live
  :class:`~src.mcp_server.protocol_handler.ProtocolHandler` registry —
  capabilities never advertise a tool that was not registered;
* limits come from the active
  :class:`~src.application.contracts.ResponseBudget`;
* retrieval vocabulary (modes, score stages, tag operators, warning and
  error codes) comes from application contract enums/dataclasses;
* rerank state comes from the loaded settings.

The only hand-curated list is ``unsupported``: a stable roadmap
catalogue (task 03+) of things deliberately absent in Task 02. It is
explicitly labelled ``status`` and never implies a registered feature.

The document is deterministic (no timestamps, registry order) so it can
be frozen as a test fixture.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any

from src.application.contracts import (
    CONTRACT_VERSION,
    ErrorCode,
    EvidenceFilterV1,
    EvidenceScores,
    ResponseBudget,
    TagOperator,
    WarningCode,
    to_jsonable,
)
from src.mcp_server.transports import SUPPORTED_TRANSPORTS

if TYPE_CHECKING:  # pragma: no cover
    from src.mcp_server.protocol_handler import ProtocolHandler

CAPABILITIES_URI = "rag://server/capabilities"
CAPABILITIES_RESOURCE_NAME = "SKDY read-only MCP capabilities"
CAPABILITIES_MIME_TYPE = "application/json"

# Retrieval modes accepted by the application query service today
# (src.application.services.query_service.QueryService.search).
RETRIEVAL_MODES = ("hybrid", "dense", "sparse")

# Evidence content types the v1 pipeline can currently produce.
CONTENT_TYPES = ("text",)

# Tool name → lifecycle. The one compat alias is a deliberate, frozen
# exception; every new long-term tool is "long_term".
_COMPAT_ALIAS_TOOLS = frozenset({"get_document_summary"})

# Roadmap catalogue of deliberately-unsupported capabilities (Task 03+).
# Hand-curated on purpose; "enabled" facts for real tools come from the
# registry above, never from this list. This single table is also the
# source of the explicit ``features`` false-flags below, so a capability
# cannot be advertised as both implemented and planned.
UNSUPPORTED: tuple[dict[str, str | None], ...] = (
    {"id": "list_documents", "planned_in": "task-03",
     "status": "planned"},
    {"id": "get_chunk", "planned_in": "task-03",
     "status": "planned"},
    {"id": "search_chunks", "planned_in": "task-04",
     "status": "planned"},
    {"id": "governance_filters", "planned_in": "task-04",
     "status": "contract_defined_only"},
    {"id": "multi_query", "planned_in": "task-05",
     "status": "contract_defined_only"},
    {"id": "alternate_queries", "planned_in": "task-05",
     "status": "contract_defined_only"},
    {"id": "multi_collection_search", "planned_in": "task-05",
     "status": "planned"},
    {"id": "parent_child_chunks", "planned_in": "task-06",
     "status": "planned"},
    {"id": "source_assets", "planned_in": "task-06",
     "status": "planned"},
    {"id": "chunk_context_resources", "planned_in": "task-06",
     "status": "planned"},
    {"id": "write_tools", "planned_in": None,
     "status": "excluded_by_architecture"},
    {"id": "answer_generation", "planned_in": None,
     "status": "excluded_by_architecture"},
)

# Filter dimensions defined by EvidenceFilterV1 (field → wire dimension).
_FILTER_FIELD_TO_DIMENSION = (
    ("collection_ids", "collection"),
    ("document_ids", "document"),
    ("tag_ids", "tag"),
    ("folder_id", "folder"),
    ("file_types", "file"),
    ("content_types", "content"),
    ("source_types", "source"),
    ("updated_after", "time"),
    ("updated_before", "time"),
)


def _tools_section(handler: ProtocolHandler) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for name in handler.list_names():
        registration = handler.get(name)
        lifecycle = (
            "compat_alias" if name in _COMPAT_ALIAS_TOOLS else "long_term"
        )
        tools.append({
            "name": name,
            "version": "v1",
            "lifecycle": lifecycle,
            "structured_output": bool(
                registration is not None and registration.output_schema,
            ),
        })
    return tools


def _filter_dimensions_defined() -> list[str]:
    valid_fields = {f.name for f in dataclasses.fields(EvidenceFilterV1)}
    dimensions: list[str] = []
    for field_name, dimension in _FILTER_FIELD_TO_DIMENSION:
        if field_name in valid_fields and dimension not in dimensions:
            dimensions.append(dimension)
    return dimensions


def _features_section() -> dict[str, bool]:
    """Explicit ``false`` flags for every capability that is absent.

    Derived from :data:`UNSUPPORTED` so "not implemented" is declared
    exactly once; implemented features are visible in ``tools`` instead
    of a second hand-maintained boolean list.
    """
    return {str(item["id"]): False for item in UNSUPPORTED}


def build_capabilities(
    handler: ProtocolHandler,
    *,
    budget: ResponseBudget | None = None,
    rerank_backend: str = "none",
    server_name: str | None = None,
    transports: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build the deterministic capability document from real facts."""
    budget = budget or ResponseBudget()
    rerank_enabled = bool(rerank_backend and rerank_backend != "none")
    document: dict[str, Any] = {
        "contract": "mcp-readonly",
        "contract_version": "v1",
        "evidence_contract": CONTRACT_VERSION,
        "read_only": True,
        "capability_resource": CAPABILITIES_URI,
        "transports": list(transports or SUPPORTED_TRANSPORTS),
        "tools": _tools_section(handler),
        "features": _features_section(),
        "retrieval": {
            "modes": list(RETRIEVAL_MODES),
            "rerank": {
                "enabled": rerank_enabled,
                "backend": rerank_backend if rerank_enabled else "none",
            },
        },
        "evidence": {
            "content_types": list(CONTENT_TYPES),
            "score_stages": [
                f.name for f in dataclasses.fields(EvidenceScores)
            ],
            "matched_queries": {
                "current_max": 1,
                "reserved_max": budget.alternate_query_max_count,
            },
        },
        "filters": {
            "tag_operators": [op.value for op in TagOperator],
            "dimensions_defined": _filter_dimensions_defined(),
            "enforced_by_tools": False,
        },
        "pagination": {
            "top_k": {
                "min": 1,
                "max": budget.top_k_max,
                "default": budget.top_k_default,
            },
            "page_size": {
                "min": 1,
                "max": budget.page_size_max,
                "default": budget.page_size_default,
            },
        },
        "limits": to_jsonable(budget),
        "warnings": [code.value for code in WarningCode],
        "errors": [code.value for code in ErrorCode],
        "unsupported": [dict(item) for item in UNSUPPORTED],
    }
    if server_name:
        # Identity is provided by the caller (loaded settings) so the
        # capability document cannot disagree with serverInfo.name.
        document["server_name"] = server_name
    return document


def capabilities_json(document: dict[str, Any]) -> str:
    """Stable JSON serialization for the resource body."""
    return json.dumps(
        document, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"), sort_keys=False,
    )


def capability_handlers(document: dict[str, Any]) -> dict[str, Any]:
    """Build the resource handlers for an mcp 2.x ``Server`` constructor.

    The installed SDK (``mcp==2.2.0``, verified with a minimal probe)
    exposes resources through **constructor keyword arguments** —
    ``Server(name, on_list_resources=..., on_read_resource=...)`` — and
    has no ``@server.list_resources()`` decorator. The returned mapping
    is therefore splatted into the ``Server(...)`` call in
    :meth:`ProtocolHandler.build_server`, which keeps registration
    transport-agnostic (stdio and streamable-http share one ``Server``).

    The resource is static (one fixed URI, no templates); passing no
    handlers reproduces the exact pre-Task-02 server, which is the
    rollback switch.
    """
    from mcp.types import (
        ListResourcesResult,
        ReadResourceRequestParams,
        ReadResourceResult,
        Resource,
        TextResourceContents,
    )

    body = capabilities_json(document)

    async def on_list_resources(ctx: Any, params: Any) -> ListResourcesResult:
        return ListResourcesResult(resources=[Resource(
            uri=CAPABILITIES_URI,
            name=CAPABILITIES_RESOURCE_NAME,
            description=(
                "Versioned read-only contract and capability discovery "
                "(tools, transports, retrieval modes, filters, limits, "
                "error and warning codes)."
            ),
            mimeType=CAPABILITIES_MIME_TYPE,
        )])

    async def on_read_resource(
        ctx: Any, params: ReadResourceRequestParams,
    ) -> ReadResourceResult:
        if str(params.uri).rstrip("/") != CAPABILITIES_URI:
            # Raised, not returned: the library turns it into a
            # protocol-level error (unknown resource is not a payload).
            raise ValueError(f"unknown resource: {params.uri}")
        return ReadResourceResult(contents=[TextResourceContents(
            uri=CAPABILITIES_URI, text=body,
            mimeType=CAPABILITIES_MIME_TYPE,
        )])

    return {
        "on_list_resources": on_list_resources,
        "on_read_resource": on_read_resource,
    }


__all__ = [
    "CAPABILITIES_MIME_TYPE",
    "CAPABILITIES_RESOURCE_NAME",
    "CAPABILITIES_URI",
    "RETRIEVAL_MODES",
    "UNSUPPORTED",
    "build_capabilities",
    "capabilities_json",
    "capability_handlers",
]
