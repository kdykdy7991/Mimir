"""Unit tests for MCP collection authorization rules."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.mcp_server.auth.authorization import (
    CollectionAccessDenied,
    CollectionSelectionRequired,
    filter_accessible_collections,
    require_collection_access,
    resolve_query_collection,
)
from src.mcp_server.auth.context import TrustedLocalPrincipal


@dataclass(frozen=True)
class _Principal:
    key_id: str = "test-key"
    name: str = "test-agent"
    allowed_collections: frozenset[str] = frozenset({"hr", "policy"})


def test_explicitly_allowed_collection_is_selected():
    assert resolve_query_collection(_Principal(), "hr") == "hr"


def test_explicitly_forbidden_collection_is_rejected():
    with pytest.raises(CollectionAccessDenied):
        resolve_query_collection(_Principal(), "finance")


def test_multiple_collection_key_must_select_a_collection():
    with pytest.raises(CollectionSelectionRequired):
        resolve_query_collection(_Principal(), None)


def test_single_collection_key_selects_its_only_collection():
    principal = _Principal(allowed_collections=frozenset({"finance"}))
    assert resolve_query_collection(principal, None) == "finance"


def test_listing_hides_unauthorized_collections():
    assert filter_accessible_collections(
        _Principal(), ["finance", "hr", "policy"],
    ) == ["hr", "policy"]


def test_stdio_principal_keeps_legacy_default_and_full_access():
    principal = TrustedLocalPrincipal()
    assert resolve_query_collection(principal, None) == "default"
    require_collection_access(principal, "finance")
