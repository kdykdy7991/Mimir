"""
Task 02.3 — unified budget/schema source, runtime enforcement,
rate_limited/overloaded error plane, and v1 output bounding.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.application.contracts import (
    EvidenceScores,
    EvidenceV1,
    ResponseBudget,
    SourceLocator,
    to_json,
)
from src.core.settings import McpLimitsSettings, Settings, load_settings
from src.mcp_server.clients.errors import (
    OverloadedError,
    RateLimitedError,
)
from src.mcp_server.clients.models import (
    Diagnostics,
    KnowledgeQueryResult,
)
from src.mcp_server.presentation import (
    active_budget,
    bound_evidence_page,
    budget_from_settings,
    build_chunks_input_schema,
    build_query_input_schema,
    reset_active_budget,
    set_active_budget,
)
from src.mcp_server.presentation.errors import tool_result_for_new_error
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import query_knowledge_hub as qkh
from src.mcp_server.tools import get_document_chunks as gdc
from src.mcp_server.tools import get_document as gd
from src.mcp_server.tools import list_collections as lc

TESTS_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = TESTS_ROOT / "fixtures" / "mcp_contract" / "readonly_v1_inventory.json"


def _run(coro):
    return asyncio.run(coro)


def _query_result():
    return KnowledgeQueryResult(
        query="q", collection="default", count=0, evidence=[],
        diagnostics=Diagnostics(),
    )


class _QueryClient:
    def __init__(self, raises=None):
        self.raises = raises
        self.request = None

    def query_knowledge(self, request, principal):
        self.request = request
        if self.raises:
            raise self.raises
        return _query_result()


# ---------------------------------------------------------------------------
# Schema / settings single source
# ---------------------------------------------------------------------------

def test_default_schemas_match_frozen_inventory():
    inventory = json.loads(FIXTURE.read_text(encoding="utf-8"))
    tools = inventory["tools"]
    assert build_query_input_schema(ResponseBudget()) == (
        tools["query_knowledge_hub"]["input_schema"]
    )
    assert build_chunks_input_schema(ResponseBudget()) == (
        tools["get_document_chunks"]["input_schema"]
    )


def test_settings_round_trip_defaults_and_rejects_invalid_combinations():
    assert budget_from_settings(Settings()) == ResponseBudget()
    with pytest.raises(ValidationError):
        McpLimitsSettings(top_k_default=99)
    with pytest.raises(ValidationError):
        McpLimitsSettings(max_content_chars=999_999)
    with pytest.raises(ValidationError):
        McpLimitsSettings(query_max_length=0)


def test_budget_env_override_via_settings(tmp_path, monkeypatch):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("mcp_limits:\n  top_k_max: ${MCP_TOP_K_MAX}\n")
    monkeypatch.setenv("MCP_TOP_K_MAX", "42")
    settings = load_settings(str(cfg))
    assert budget_from_settings(settings).top_k_max == 42


def test_active_budget_drives_registered_schema():
    try:
        set_active_budget(ResponseBudget(
            top_k_default=5, top_k_max=33, page_size_default=10,
        ))
        handler = ProtocolHandler()
        qkh.register(handler)
        gdc.register(handler)
        top_prop = handler.get("query_knowledge_hub").input_schema["properties"]["top_k"]
        assert top_prop["maximum"] == 33
        assert top_prop["default"] == 5
        page_prop = handler.get(
            "get_document_chunks",
        ).input_schema["properties"]["page_size"]
        assert page_prop["default"] == 10
    finally:
        reset_active_budget()
    # Reset restores the frozen defaults.
    assert active_budget() == ResponseBudget()


# ---------------------------------------------------------------------------
# Runtime validation matches the schema
# ---------------------------------------------------------------------------

def test_query_runtime_top_k_bounds():
    # Over-limit → stable invalid_request (tool-level), client never called.
    result = _run(qkh._query_knowledge_hub(
        {"query": "q", "top_k": 51, "_client": _QueryClient()},
    ))
    assert result.is_error
    assert "between 1 and 50" in result.content[0].text

    result = _run(qkh._query_knowledge_hub(
        {"query": "q", "top_k": "abc", "_client": _QueryClient()},
    ))
    assert result.is_error
    assert result.content[0].text == "'top_k' must be an integer"

    client = _QueryClient()
    _run(qkh._query_knowledge_hub(
        {"query": "q", "top_k": 50, "_client": client},
    ))
    assert client.request.top_k == 50


def test_chunks_runtime_page_bounds_match_legacy_wording():
    result = _run(gdc._get_document_chunks({
        "document_id": "d", "page_size": 51,
        "_client": object(),
    }))
    assert result.is_error
    assert result.content[0].text == "page_size must be between 1 and 50"

    result = _run(gdc._get_document_chunks({
        "document_id": "d", "page": 0,
        "_client": object(),
    }))
    assert result.is_error
    assert result.content[0].text == "page must be >= 1"

    result = _run(gdc._get_document_chunks({
        "document_id": "d", "page": "x",
        "_client": object(),
    }))
    assert result.is_error
    assert result.content[0].text == "'page' and 'page_size' must be integers"


# ---------------------------------------------------------------------------
# rate_limited / overloaded — contract + tool-level mapping
# ---------------------------------------------------------------------------

def test_tool_result_for_new_error_messages():
    result = tool_result_for_new_error(RateLimitedError(
        "slow", retry_after_seconds=15,
    ))
    assert result.is_error
    assert result.content[0].text.startswith("rate_limited:")
    assert "15 seconds" in result.content[0].text

    result = tool_result_for_new_error(OverloadedError())
    assert result.is_error
    assert result.content[0].text.startswith("overloaded:")

    assert tool_result_for_new_error(ValueError("x")) is None


@pytest.mark.parametrize("exc,prefix", [
    (RateLimitedError(), "rate_limited:"),
    (OverloadedError(), "overloaded:"),
])
def test_all_four_tools_map_new_errors(exc, prefix):
    # query
    result = _run(qkh._query_knowledge_hub(
        {"query": "q", "_client": _QueryClient(raises=exc)},
    ))
    assert result.is_error and result.content[0].text.startswith(prefix)

    # chunks
    class ChunksClient:
        def get_document_chunks(self, *a):
            raise exc
    result = _run(gdc._get_document_chunks({
        "document_id": "d", "_client": ChunksClient(),
    }))
    assert result.is_error and result.content[0].text.startswith(prefix)

    # get document (shared handler with get_document_summary)
    class DocClient:
        def get_document(self, *a):
            raise exc
    result = _run(gd._get_document_item({
        "document_id": "d", "_client": DocClient(),
    }))
    assert result.is_error and result.content[0].text.startswith(prefix)

    # collections
    class ListClient:
        def list_collections(self, *a):
            raise exc
    result = _run(lc._list_collections({"_client": ListClient()}))
    assert result.is_error and result.content[0].text.startswith(prefix)


def test_http_429_maps_to_rate_limited():
    import httpx
    from src.mcp_server.clients.http_client import HttpRagReadOnlyClient
    from src.mcp_server.auth.context import TrustedLocalPrincipal

    def handler(request):
        return httpx.Response(
            429, json={"code": "rate_limited", "message": "too many"},
            headers={"Retry-After": "20"},
        )

    client = HttpRagReadOnlyClient(
        base_url="http://api:8000", api_key="k",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RateLimitedError) as exc_info:
        client.list_collections(TrustedLocalPrincipal())
    assert exc_info.value.retry_after_seconds == 20.0
    assert not isinstance(exc_info.value, OverloadedError)


def test_http_explicit_overloaded_code_maps_distinctly():
    import httpx
    from src.mcp_server.clients.http_client import HttpRagReadOnlyClient
    from src.mcp_server.auth.context import TrustedLocalPrincipal

    def handler(request):
        return httpx.Response(503, json={"code": "overloaded", "message": "busy"})

    client = HttpRagReadOnlyClient(
        base_url="http://api:8000", api_key="k",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OverloadedError):
        client.list_collections(TrustedLocalPrincipal())


# ---------------------------------------------------------------------------
# v1 output bounding
# ---------------------------------------------------------------------------

def _v1_evidence(idx, *, content_len=0, preview=None):
    return EvidenceV1(
        collection_id="kb", document_id="d", chunk_id=f"c{idx}",
        content_type="text",
        source_locator=SourceLocator(kind="page", page=idx + 1),
        scores=EvidenceScores(fusion=0.1 * idx),
        matched_queries=("q",),
        content=("z" * content_len) if content_len else None,
        content_preview=preview,
    )


def test_bounded_page_within_budget_has_no_flags():
    rows = [_v1_evidence(i, content_len=100) for i in range(3)]
    page = bound_evidence_page(rows, ResponseBudget())
    payload = json.loads(to_json(page.to_dict()))
    assert payload["returned_count"] == 3
    assert payload["truncated_results"] is False
    assert payload["truncated_characters"] is False
    assert payload["warnings"] == []


def test_bounded_page_caps_row_count_with_warning():
    budget = ResponseBudget(
        top_k_default=2, top_k_max=2, max_evidence_count=2,
    )
    page = bound_evidence_page(
        [_v1_evidence(i) for i in range(5)], budget,
    )
    payload = json.loads(to_json(page.to_dict()))
    assert payload["returned_count"] == 2
    assert payload["truncated_results"] is True
    assert payload["truncated_characters"] is False
    assert [w["code"] for w in payload["warnings"]] == ["truncated"]
    assert [r["chunk_id"] for r in payload["results"]] == ["c0", "c1"]


def test_bounded_page_shrinks_for_structured_char_budget():
    # One empty v1 identity row is ~580 chars (all optional keys), so the
    # structured budget must fit the identity envelope plus some body.
    budget = ResponseBudget(
        max_structured_chars=1500,
        max_content_chars=200,
        max_preview_chars=80,
    )
    rows = [_v1_evidence(i, content_len=5000) for i in range(3)]
    page = bound_evidence_page(rows, budget)
    serialized = to_json(page.to_dict())
    assert len(serialized) <= budget.max_structured_chars
    payload = json.loads(serialized)
    assert payload["truncated_characters"] is True
    assert [w["code"] for w in payload["warnings"]] == ["truncated"]


def test_bounded_page_is_deterministic():
    rows = [_v1_evidence(i, content_len=3000) for i in range(4)]
    budget = ResponseBudget(
        max_structured_chars=1800, max_content_chars=100,
        max_preview_chars=40, top_k_default=4, top_k_max=4,
    )
    first = bound_evidence_page(rows, budget)
    second = bound_evidence_page(list(rows), budget)
    assert to_json(first.to_dict()) == to_json(second.to_dict())
    assert len(to_json(first.to_dict())) <= budget.max_structured_chars
