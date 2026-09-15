"""
Active response budget + schema builders (Task 02.3).

Bridges :class:`src.core.settings.McpLimitsSettings` (operator
configuration) to the application-owned
:class:`src.application.contracts.ResponseBudget` (single enforcement
source). Tool JSON Schemas are *built* from the same budget the runtime
validators use, so declared min/max/default and enforcement can never
drift apart.

The active budget is process-wide (MCP servers are single-tenant per
process) and set once at boot from loaded settings; tests call
:func:`reset_active_budget` for isolation. At default settings the
generated query/chunks input schemas are byte-identical to the schemas
frozen in the v1 inventory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.application.contracts import ResponseBudget

if TYPE_CHECKING:  # pragma: no cover
    from src.core.settings import Settings

_active_budget = ResponseBudget()


def active_budget() -> ResponseBudget:
    """Return the budget installed at boot (defaults if none set)."""
    return _active_budget


def set_active_budget(budget: ResponseBudget) -> None:
    """Install the process budget (called once during server bootstrap)."""
    global _active_budget
    if not isinstance(budget, ResponseBudget):
        raise TypeError("budget must be a ResponseBudget")
    _active_budget = budget


def reset_active_budget() -> None:
    """Restore defaults (test isolation)."""
    global _active_budget
    _active_budget = ResponseBudget()


def budget_from_settings(settings: Settings) -> ResponseBudget:
    """Convert validated settings into the application budget."""
    limits = settings.mcp_limits
    return ResponseBudget(
        query_max_length=limits.query_max_length,
        top_k_default=limits.top_k_default,
        top_k_max=limits.top_k_max,
        page_size_default=limits.page_size_default,
        page_size_max=limits.page_size_max,
        max_evidence_count=limits.max_evidence_count,
        max_structured_chars=limits.max_structured_chars,
        max_content_chars=limits.max_content_chars,
        max_preview_chars=limits.max_preview_chars,
        alternate_query_max_count=limits.alternate_query_max_count,
        alternate_query_total_max_chars=limits.alternate_query_total_max_chars,
    )


def build_query_input_schema(budget: ResponseBudget) -> dict[str, Any]:
    """Build ``query_knowledge_hub`` input schema from one budget."""
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": budget.query_max_length,
                "description": (
                    "The text to retrieve evidence for. Up to "
                    f"{budget.query_max_length} characters."
                ),
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": budget.top_k_max,
                "default": budget.top_k_default,
                "description": "Maximum number of evidence results to return.",
            },
            "collection": {
                "type": "string",
                "default": "default",
                "description": "Collection name (= BM25 index name) to query.",
            },
            "rerank": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Whether to apply the optional rerank stage when one is "
                    "configured. Highly recommended for precision."
                ),
            },
            "no_rerank": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Deprecated alias for `rerank`. Use `rerank` instead."
                ),
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def build_chunks_input_schema(budget: ResponseBudget) -> dict[str, Any]:
    """Build ``get_document_chunks`` input schema from one budget."""
    return {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "The stable document UUID.",
            },
            "doc_id": {
                "type": "string",
                "description": "Deprecated alias for `document_id`.",
            },
            "page": {
                "type": "integer",
                "minimum": 1,
                "default": 1,
                "description": "1-based page number.",
            },
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": budget.page_size_max,
                "default": budget.page_size_default,
                "description": f"Chunks per page (1..{budget.page_size_max}).",
            },
        },
        "oneOf": [
            {"required": ["document_id"]},
            {"required": ["doc_id"]},
        ],
        "additionalProperties": False,
    }
