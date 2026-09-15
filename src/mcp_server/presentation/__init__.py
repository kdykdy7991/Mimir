"""MCP presentation layer (Task 02.2).

Central mapping from application contracts to MCP structured content.
Nothing in :mod:`src.application` imports this package — the dependency
direction is application → presentation, never reversed (ADR 0001 §3.3).
"""

from __future__ import annotations

from src.mcp_server.presentation.evidence_mapper import (
    LEGACY_EMPTY_HINT,
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
    "evidence_v1_from_legacy",
    "format_query_result",
    "query_structured",
    "redact_sensitive",
    "safe_preview",
    "v1_evidence_row",
    "warnings_from_diagnostics",
]
