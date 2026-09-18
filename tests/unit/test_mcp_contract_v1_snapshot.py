"""
Drift gate for the MCP read-only contract v1.

Two layers, deliberately redundant so a contract change cannot sneak through
by editing generator + fixture together:

1. Whole-inventory snapshot: the committed JSON fixture
   (``tests/fixtures/mcp_contract/readonly_v1_inventory.json``) must equal the
   inventory generated from the LIVE tool registry
   (``src.mcp_server.server._register_default_tools`` — the exact registry
   served by MCP ``tools/list`` on stdio and streamable-http).
2. Point assertions against the live registry itself for the compatibility
   surface external clients depend on: tool set, required fields, ``oneOf``
   branches, compat aliases, numeric bounds/defaults and the (currently
   empty) enum/const set.

Regenerate after a *reviewed* contract change:

    MCP_REGENERATE_V1=1 .venv/bin/python -m pytest tests/unit/test_mcp_contract_v1_snapshot.py

This module is offline: registration binds schemas only and builds no
retrieval stack. The older Phase-0 snapshot (``test_mcp_contract_snapshot.py``)
remains in force; the two are complementary.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.mcp_contract_inventory import build_inventory, dump
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.server import _register_default_tools

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "mcp_contract" / "readonly_v1_inventory.json"
)

EXPECTED_TOOLS = frozenset({
    "get_document",
    "get_document_chunks",
    "get_document_summary",
    "get_chunk",
    "get_chunk_context",
    "list_collections",
    "list_data_sources",
    "list_documents",
    "list_sync_failures",
    "get_sync_status",
    "query_knowledge_hub",
    "search_chunks",
})

REGENERATE = os.environ.get("MCP_REGENERATE_V1") == "1"


@pytest.fixture(scope="module")
def handler() -> ProtocolHandler:
    h = ProtocolHandler(server_name="contract-v1-test")
    _register_default_tools(h)
    return h


@pytest.fixture(scope="module")
def inventory() -> dict:
    return build_inventory()


# --------------------------------------------------------------------------
# Layer 1: whole-inventory snapshot
# --------------------------------------------------------------------------

def test_inventory_fixture_matches_live_registry():
    rendered = dump(build_inventory())
    if REGENERATE:
        FIXTURE.write_text(rendered, encoding="utf-8")
        pytest.skip(f"regenerated {FIXTURE}")
    assert FIXTURE.is_file(), f"missing committed contract fixture: {FIXTURE}"
    committed = FIXTURE.read_text(encoding="utf-8")
    assert committed == rendered, (
        "MCP read-only v1 contract drifted from the committed fixture. "
        "Review the diff; if intentional, regenerate with MCP_REGENERATE_V1=1 "
        "and update docs/contracts/mcp-readonly-v1.md."
    )


def test_committed_fixture_is_valid_json_with_expected_envelope():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["contract"] == "mcp-readonly"
    assert data["contract_version"] == "v1"
    assert data["read_only"] is True
    assert data["tool_count"] == len(EXPECTED_TOOLS)
    assert set(data["tools"]) == EXPECTED_TOOLS


# --------------------------------------------------------------------------
# Layer 2: point assertions over the live registry
# --------------------------------------------------------------------------

def test_registered_tool_set_is_exactly_frozen(handler):
    assert set(handler.list_names()) == EXPECTED_TOOLS


def _schema(handler, name):
    reg = handler.get(name)
    assert reg is not None
    return reg.input_schema, reg.output_schema or {}


def test_query_tool_required_fields_and_limits(handler):
    inp, out = _schema(handler, "query_knowledge_hub")
    assert inp.get("required") == ["query"]
    assert inp.get("additionalProperties") is False
    query = inp["properties"]["query"]
    assert query["type"] == "string"
    assert query.get("minLength") == 1
    assert query.get("maxLength") == 2000
    top_k = inp["properties"]["top_k"]
    assert top_k.get("minimum") == 1
    assert top_k.get("maximum") == 50
    assert top_k.get("default") == 10
    # rerank is the canonical switch; no_rerank stays as a deprecated alias.
    assert "rerank" in inp["properties"]
    assert inp["properties"]["rerank"].get("default") is True
    assert "no_rerank" in inp["properties"]

    assert set(out.get("required", [])) >= {
        "query", "collection", "count", "evidence", "diagnostics",
    }
    # Output compat aliases must remain until the deprecation window closes.
    for alias in ("n_results", "citations"):
        assert alias in out.get("properties", {})
    evidence_item = out["properties"]["evidence"]["items"]
    assert set(evidence_item.get("required", [])) == {
        "rank", "chunk_id", "document_id", "source", "text",
    }


def test_list_collections_empty_input_and_count_alias(handler):
    inp, out = _schema(handler, "list_collections")
    assert inp.get("properties", {}) == {}
    assert inp.get("additionalProperties") is False
    assert "required" not in inp
    assert set(out.get("required", [])) >= {"count", "collections"}
    assert "n_collections" in out.get("properties", {})
    item = out["properties"]["collections"]["items"]
    assert item.get("required") == ["name"]


def test_get_document_oneof_and_compat_aliases(handler):
    inp, out = _schema(handler, "get_document")
    branches = [sorted(clause.get("required", [])) for clause in inp.get("oneOf", [])]
    assert sorted(branches) == [["doc_id"], ["document_id"]]
    assert "required" not in inp  # oneOf, not a document_id-only top-level required
    assert inp.get("additionalProperties") is False
    for alias in ("doc_id",):
        assert alias in inp["properties"]
    assert set(out.get("required", [])) == {
        "document_id", "title", "summary", "tags", "source", "doc_type",
        "chunk_count",
    }
    for alias in ("doc_id", "doc_type", "source_path"):
        assert alias in out.get("properties", {})


def test_get_document_summary_is_doc_alias_with_required_doc_id(handler):
    inp, out = _schema(handler, "get_document_summary")
    assert inp.get("required") == ["doc_id"]
    assert inp.get("additionalProperties") is False
    for alias in ("doc_id", "doc_type", "source_path"):
        assert alias in out.get("properties", {})
    # The alias tool must keep returning the canonical document_id field too.
    assert "document_id" in out.get("properties", {})


def test_get_document_chunks_oneof_pagination_and_ordering_contract(handler):
    inp, out = _schema(handler, "get_document_chunks")
    branches = [sorted(clause.get("required", [])) for clause in inp.get("oneOf", [])]
    assert sorted(branches) == [["doc_id"], ["document_id"]]
    assert inp.get("additionalProperties") is False
    page = inp["properties"]["page"]
    assert page.get("minimum") == 1
    assert page.get("default") == 1
    page_size = inp["properties"]["page_size"]
    assert page_size.get("minimum") == 1
    assert page_size.get("maximum") == 50
    assert page_size.get("default") == 20

    assert set(out.get("required", [])) == {
        "document_id", "page", "page_size", "total", "has_next", "chunks",
    }
    item = out["properties"]["chunks"]["items"]
    assert set(item.get("required", [])) >= {"chunk_id", "index"}


def test_only_reviewed_enum_constraints_exist_v1(handler):
    found: list[str] = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("enum", "const"):
                    found.append(f"{path}.{key}")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    for name in sorted(handler.list_names()):
        inp, out = _schema(handler, name)
        walk(inp, f"{name}.input")
        walk(out, f"{name}.output")
    assert found == [
        "get_chunk_context.input.properties.include.enum",
        "list_documents.input.properties.status.enum",
        "list_documents.input.properties.tag_operator.enum",
        "list_documents.input.properties.sort.enum",
        "search_chunks.input.properties.failure_policy.enum",
        "search_chunks.input.properties.mode.enum",
        "search_chunks.input.properties.filters.properties.tag_operator.enum",
    ], f"new enum/const constraints require contract review: {found}"


# --------------------------------------------------------------------------
# Curated operational metadata
# --------------------------------------------------------------------------

def test_curated_lifecycle_and_backends_are_declared(inventory):
    for name, tool in inventory["tools"].items():
        assert tool["lifecycle"] in {"long_term", "compat_alias"}, name
        backends = tool["backends"]
        assert set(backends) == {
            "stdio_mcp", "streamable_http_mcp",
            "in_process_client", "standalone_http_client",
        }
        assert all(backends.values()), name
        assert tool["errors"], name  # error vocabulary stays documented
    assert (
        inventory["tools"]["get_document_summary"]["lifecycle"]
        == "compat_alias"
    )


def test_every_declared_alias_is_marked_deprecated_compat(handler, inventory):
    """Aliases in the curated inventory must reference real schema fields;
    the generator's _check_expect enforces this at build time, so simply
    building the inventory is the cross-check here."""
    alias_tools = {
        "query_knowledge_hub": {"no_rerank", "n_results", "citations"},
        "list_collections": {"n_collections"},
        "get_document": {"doc_id", "doc_type", "source_path"},
        "get_document_summary": {"doc_id", "doc_type", "source_path"},
        "get_document_chunks": {"doc_id"},
    }
    for name, expected_aliases in alias_tools.items():
        declared = {a["alias"] for a in inventory["tools"][name]["compat_aliases"]}
        assert expected_aliases <= declared, (name, declared)
