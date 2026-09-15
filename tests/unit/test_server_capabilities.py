"""
Task 02.4 — machine-readable capability discovery.

Covers: the capability document is derived from the real registry and
active budget (never advertises an unregistered tool), vocabulary is
derived from application contracts, output is deterministic and frozen
as a fixture, the static rag:// resource round-trips over a real MCP
client/server session (mcp 2.x), and rollback (no Resource) is one flag.
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
import json
import os
import re
from pathlib import Path

import pytest

from src.application.contracts import (
    ErrorCode,
    EvidenceFilterV1,
    EvidenceScores,
    ResponseBudget,
    TagOperator,
    WarningCode,
)
from src.mcp_server.capabilities import (
    CAPABILITIES_MIME_TYPE,
    CAPABILITIES_URI,
    UNSUPPORTED,
    build_capabilities,
    capabilities_json,
)
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.server import _register_default_tools, parse_args
from src.mcp_server.transports import SUPPORTED_TRANSPORTS

TESTS_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = TESTS_ROOT / "fixtures" / "mcp_contract" / "capabilities_v1.json"


def _registry() -> ProtocolHandler:
    handler = ProtocolHandler()
    _register_default_tools(handler)
    return handler


@pytest.fixture(scope="module")
def document() -> dict:
    return build_capabilities(
        _registry(), budget=ResponseBudget(), rerank_backend="none",
    )


# ---------------------------------------------------------------------------
# Document content — real facts only
# ---------------------------------------------------------------------------

def test_capabilities_lists_exactly_the_registered_tools(document):
    registry = _registry()
    assert [t["name"] for t in document["tools"]] == registry.list_names()
    assert registry.list_names() == [
        "query_knowledge_hub",
        "list_collections",
        "get_document",
        "get_document_summary",
        "get_document_chunks",
    ]
    by_name = {t["name"]: t for t in document["tools"]}
    assert by_name["get_document_summary"]["lifecycle"] == "compat_alias"
    assert by_name["query_knowledge_hub"]["lifecycle"] == "long_term"
    assert all(t["version"] == "v1" for t in document["tools"])
    assert all(t["structured_output"] is True for t in document["tools"])


def test_capabilities_advertise_no_unregistered_tool(document):
    registered = {t["name"] for t in document["tools"]}
    planned = {item["id"] for item in document["unsupported"]}
    assert registered.isdisjoint(planned)
    for item in UNSUPPORTED:
        assert item["status"] in {
            "planned", "contract_defined_only", "excluded_by_architecture",
        }


def test_capabilities_limits_match_active_budget(document):
    budget = ResponseBudget()
    assert document["limits"] == dataclasses.asdict(budget)
    assert document["pagination"] == {
        "top_k": {"min": 1, "max": 50, "default": 10},
        "page_size": {"min": 1, "max": 50, "default": 20},
    }


def test_capabilities_vocabulary_comes_from_contracts(document):
    assert document["evidence"]["score_stages"] == [
        f.name for f in dataclasses.fields(EvidenceScores)
    ]
    assert document["warnings"] == [c.value for c in WarningCode]
    assert document["errors"] == [c.value for c in ErrorCode]
    assert document["filters"]["tag_operators"] == [
        op.value for op in TagOperator
    ]
    # Every advertised filter dimension is a real EvidenceFilterV1 field.
    filter_fields = {f.name for f in dataclasses.fields(EvidenceFilterV1)}
    assert {
        "collection", "document", "tag", "folder",
        "file", "content", "source", "time",
    }.issubset(set(document["filters"]["dimensions_defined"]))
    assert document["filters"]["enforced_by_tools"] is False


def test_capabilities_retrieval_and_identity_fields(document):
    assert document["contract"] == "mcp-readonly"
    assert document["contract_version"] == "v1"
    assert document["evidence_contract"] == "evidence-v1"
    assert document["read_only"] is True
    assert document["capability_resource"] == CAPABILITIES_URI
    assert document["retrieval"]["modes"] == ["hybrid", "dense", "sparse"]
    assert document["retrieval"]["rerank"] == {
        "enabled": False, "backend": "none",
    }
    assert document["evidence"]["matched_queries"]["current_max"] == 1


def test_capabilities_transports_track_cli_support(document):
    """Transports are advertised from the same constant the CLI uses."""
    assert document["transports"] == list(SUPPORTED_TRANSPORTS)
    assert set(document["transports"]) == {"stdio", "streamable-http"}
    with pytest.raises(SystemExit):  # a transport we do not advertise
        parse_args(["--transport", "sse"])
    for transport in document["transports"]:
        assert parse_args(["--transport", transport]).transport == transport


def test_capabilities_declare_absent_features_false(document):
    """Every roadmap-absent capability is an explicit, machine-readable
    ``false`` — never missing, never implied."""
    features = document["features"]
    for absent in (
        "parent_child_chunks", "source_assets", "multi_collection_search",
        "alternate_queries", "write_tools",
    ):
        assert features[absent] is False, absent
    assert all(value is False for value in features.values())
    # A capability cannot be advertised as both planned and registered.
    assert set(features) == {str(item["id"]) for item in UNSUPPORTED}


def test_capabilities_leak_no_secret_or_host_path(document):
    """No API key, credential, absolute path or provider secret."""
    blob = capabilities_json(document)
    lowered = blob.lower()
    for needle in (
        "api_key", "apikey", "authorization", "bearer ", "secret",
        "password", "token", "skdy_mcp_",
    ):
        assert needle not in lowered, needle
    # Absolute filesystem paths never appear (URIs are not paths).
    for pattern in (r"(?<![\w:/])/(?:Users|home|var|opt|tmp)/", r"[A-Za-z]:\\"):
        assert re.search(pattern, blob) is None, pattern


def test_capabilities_declare_server_identity_when_given(document):
    """Server identity is optional and never invented."""
    assert "server_name" not in document
    named = build_capabilities(
        _registry(), server_name="skdy-knowledge-query",
    )
    assert named["server_name"] == "skdy-knowledge-query"


def test_capabilities_rerank_reflects_configuration():
    doc = build_capabilities(
        _registry(), rerank_backend="cross_encoder",
    )
    assert doc["retrieval"]["rerank"] == {
        "enabled": True, "backend": "cross_encoder",
    }


def test_capabilities_are_deterministic(document):
    again = build_capabilities(
        _registry(), budget=ResponseBudget(), rerank_backend="none",
    )
    assert capabilities_json(document) == capabilities_json(again)
    assert "generated_at" not in document


# ---------------------------------------------------------------------------
# Frozen fixture
# ---------------------------------------------------------------------------

def test_capabilities_fixture_matches_live_registry(document):
    if os.getenv("MCP_REGENERATE_V1"):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(
            json.dumps(
                document, ensure_ascii=False, indent=2, sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        pytest.skip("regenerated capabilities_v1.json")
    frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert frozen == document


# ---------------------------------------------------------------------------
# MCP Resource registration over a real client/server round trip
# ---------------------------------------------------------------------------

def _mcp_major_version() -> int | None:
    try:
        return int(importlib.metadata.version("mcp").split(".", 1)[0])
    except (ValueError, importlib.metadata.PackageNotFoundError):
        return None


_NEEDS_MCP_V2 = pytest.mark.skipif(
    (_mcp_major_version() or 0) < 2,
    reason="resource handlers require the mcp 2.x Server constructor API",
)


def _client(server):
    """In-process MCP client for a built Server (mcp 2.x public API).

    ``mcp.Client`` accepts a ``Server`` instance and drives the full
    JSON-RPC session against it in memory, so the assertions below run
    against the same ``Server`` object the stdio / streamable-http
    transports serve.
    """
    from mcp import Client

    return Client(server)


@_NEEDS_MCP_V2
async def test_capability_resource_round_trips_over_mcp_session():
    document = build_capabilities(
        _registry(), budget=ResponseBudget(), rerank_backend="none",
    )
    server = _registry().build_server(capabilities=document)

    async with _client(server) as client:
        assert client.server_capabilities.resources is not None
        resources = await client.list_resources()
        uris = [str(r.uri) for r in resources.resources]
        assert uris == [CAPABILITIES_URI]
        resource = resources.resources[0]
        assert resource.mime_type == CAPABILITIES_MIME_TYPE
        assert resource.name

        read = await client.read_resource(CAPABILITIES_URI)
        served = json.loads(read.contents[0].text)
        assert served == document

        # The Resource is additive: the tool surface is unchanged.
        tools = await client.list_tools()
        assert [t.name for t in tools.tools] == [
            t["name"] for t in document["tools"]
        ]


@_NEEDS_MCP_V2
async def test_unknown_resource_uri_is_rejected():
    handler = _registry()
    server = handler.build_server(capabilities=build_capabilities(handler))

    async with _client(server) as client:
        with pytest.raises(Exception):  # protocol-level error
            await client.read_resource("rag://server/nope")


@_NEEDS_MCP_V2
async def test_build_server_without_capabilities_registers_no_resource():
    """Rollback shape: no capabilities document → no Resource at all."""
    server = _registry().build_server()
    async with _client(server) as client:
        # No resources capability is advertised, so resources/list is
        # not even a method of this server (pre-Task-02 shape).
        assert client.server_capabilities.resources is None
        with pytest.raises(Exception):  # protocol-level METHOD_NOT_FOUND
            await client.list_resources()
        # ... and the 5 tools are still served.
        assert len((await client.list_tools()).tools) == 5
