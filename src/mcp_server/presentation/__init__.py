"""MCP presentation layer (Task 02.2).

Central mapping from application contracts to MCP structured content.
Nothing in :mod:`src.application` imports this package — the dependency
direction is application → presentation, never reversed (ADR 0001 §3.3).
"""

from __future__ import annotations

from src.mcp_server.presentation.budgets import (
    active_budget,
    budget_from_settings,
    build_chunks_input_schema,
    build_query_input_schema,
    reset_active_budget,
    set_active_budget,
)
from src.mcp_server.presentation.evidence_mapper import (
    LEGACY_EMPTY_HINT,
    bound_evidence_page,
    evidence_v1_from_legacy,
    format_query_result,
    query_structured,
    redact_sensitive,
    safe_preview,
    v1_evidence_row,
    warnings_from_diagnostics,
)

__all__ = [
    "LEGACY_EMPTY_HINT",
    "active_budget",
    "bound_evidence_page",
    "budget_from_settings",
    "build_chunks_input_schema",
    "build_query_input_schema",
    "evidence_v1_from_legacy",
    "format_query_result",
    "query_structured",
    "redact_sensitive",
    "reset_active_budget",
    "safe_preview",
    "set_active_budget",
    "v1_evidence_row",
    "warnings_from_diagnostics",
]
