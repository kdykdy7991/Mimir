#!/usr/bin/env python3
"""
Generate the machine-readable MCP read-only contract inventory (v1).

Single source of truth strategy
--------------------------------
* ``name`` / ``description`` / ``input_schema`` / ``output_schema`` are
  extracted from the **real server registry** built by
  :func:`src.mcp_server.server._register_default_tools` — exactly what
  ``tools/list`` serves on both stdio and streamable-http. They are never
  retyped here.
* The per-tool operational notes (aliases, limits, errors, authorization,
  supported backends, lifecycle) are curated facts, kept next to this
  generator. Every curated claim that references schema (alias property
  names, oneOf, required, limits) is cross-checked against the live schema
  while generating, so a drift fails loudly instead of silently lying.

Usage
-----
    python scripts/mcp_contract_inventory.py --check      # exit 1 on drift
    python scripts/mcp_contract_inventory.py              # print JSON
    python scripts/mcp_contract_inventory.py --output FILE

The committed fixture lives at
``tests/fixtures/mcp_contract/readonly_v1_inventory.json`` and the
pytest wrapper (``tests/unit/test_mcp_contract_v1_snapshot.py``) regenerates
it with ``MCP_REGENERATE_V1=1``.

This module performs no network calls and instantiates no retrieval stack:
registration only binds tool functions to schemas.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONTRACT_NAME = "mcp-readonly"
CONTRACT_VERSION = "v1"
FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "mcp_contract" / "readonly_v1_inventory.json"
)

# Backend support matrix. All current tools share the same three call
# paths (stdio MCP, Bearer-authenticated streamable-http MCP, and the
# read-only application client in both in-process / standalone-http
# deployments); per-tool rows stay explicit so a future divergence is a
# deliberate inventory edit.
_ALL_BACKENDS = {
    "stdio_mcp": True,
    "streamable_http_mcp": True,
    "in_process_client": True,
    "standalone_http_client": True,
}

_CURATED: dict[str, dict[str, Any]] = {
    "list_collections": {
        "kind": "collection_discovery",
        "returns": (
            "Authorized collections as name/description/document_count/"
            "chunk_count entries. Counts may be null when backing storage "
            "is unavailable — zero is never fabricated. No disk paths."
        ),
        "canonical_params": [],
        "compat_aliases": [
            {"surface": "output", "alias": "n_collections",
             "canonical": "count", "phase_out": "compatibility window"},
        ],
        "pagination": None,
        "limits": "No arguments; additionalProperties rejected.",
        "errors": [
            {"condition": "credential has no accessible/extant collections",
             "surface": "successful empty result (count=0)",
             "message": "collections: []"},
            {"condition": "upstream statistics stores unavailable",
             "surface": "successful result with null counts",
             "message": "document_count/chunk_count = null"},
        ],
        "collection_authorization": (
            "Returns extant server collections intersected with the API "
            "key whitelist; stdio TrustedLocalPrincipal sees all. Unauthorized "
            "names, counts and existence are never leaked."
        ),
        "backends": dict(_ALL_BACKENDS),
        "lifecycle": "long_term",
    },
    "query_knowledge_hub": {
        "kind": "evidence_retrieval",
        "returns": (
            "Ranked raw evidence chunks (chunk_id, document_id, title, "
            "source, page, score, text excerpt) plus diagnostics. Returns "
            "NO synthesized answer."
        ),
        "canonical_params": ["query", "collection", "top_k", "rerank"],
        "compat_aliases": [
            {"surface": "input", "alias": "no_rerank",
             "canonical": "rerank (rerank wins when both present)",
             "phase_out": "compatibility window"},
            {"surface": "output", "alias": "n_results",
             "canonical": "count", "phase_out": "compatibility window"},
            {"surface": "output", "alias": "citations",
             "canonical": "evidence", "phase_out": "compatibility window"},
        ],
        "pagination": "Single top-k window (no cursor); 1..50, default 10.",
        "limits": (
            "query required, 1..2000 chars; top_k 1..50 (default 10); "
            "additionalProperties rejected."
        ),
        "errors": [
            {"condition": "query missing/empty",
             "surface": "tool result is_error=true",
             "message": "'query' is required and must be a non-empty string"},
            {"condition": "query longer than 2000 characters",
             "surface": "tool result is_error=true",
             "message": "'query' exceeds the 2000-character limit"},
            {"condition": "collection outside key grant",
             "surface": "tool result is_error=true",
             "message": "collection is not accessible with this credential"},
            {"condition": "multi-collection key omits collection",
             "surface": "tool result is_error=true",
             "message": (
                 "'collection' is required when this credential can access "
                 "multiple knowledge bases")},
            {"condition": "retrieval stack failure (embedding/store/BM25)",
             "surface": "protocol-level error (NOT an empty result)",
             "message": "knowledge retrieval failed: <ExceptionType>"},
        ],
        "collection_authorization": (
            "Explicit collection must be in the key grant; omitted with a "
            "single-collection grant auto-selects it; omitted with a "
            "multi-collection grant is a tool error. Authorization runs "
            "before embedding/vector/BM25/reranker construction. stdio "
            "defaults to 'default'."
        ),
        "backends": dict(_ALL_BACKENDS),
        "lifecycle": "long_term",
        "expect": {
            "required": ["query"],
            "max_query_length": 2000,
            "top_k": {"min": 1, "max": 50, "default": 10},
            "input_alias_properties": ["no_rerank"],
            "output_alias_properties": ["n_results", "citations"],
        },
    },
    "get_document": {
        "kind": "document_metadata",
        "returns": (
            "One document's metadata (title, type, source, summary, tags, "
            "chunk_count). No unbounded chunk body."
        ),
        "canonical_params": ["document_id"],
        "compat_aliases": [
            {"surface": "input", "alias": "doc_id",
             "canonical": "document_id", "phase_out": "compatibility window"},
            {"surface": "output", "alias": "doc_id",
             "canonical": "document_id", "phase_out": "compatibility window"},
            {"surface": "output", "alias": "doc_type",
             "canonical": "document_type", "phase_out": "compatibility window"},
            {"surface": "output", "alias": "source_path",
             "canonical": "source", "phase_out": "compatibility window"},
        ],
        "pagination": None,
        "limits": (
            "document_id or doc_id required (oneOf); additionalProperties "
            "rejected."
        ),
        "errors": [
            {"condition": "neither document_id nor doc_id supplied/empty",
             "surface": "tool result is_error=true",
             "message": (
                 "'document_id' is required and must be a non-empty string")},
            {"condition": "unknown id OR id in an unauthorized collection",
             "surface": "tool result is_error=true (identical shape)",
             "message": "document not found or not accessible"},
            {"condition": "invalid request from client layer",
             "surface": "tool result is_error=true",
             "message": "client InvalidRequestError text"},
        ],
        "collection_authorization": (
            "Stable document UUID resolves to (collection, source_path); "
            "collection access is re-checked before any storage read. "
            "Not-found and forbidden share one external message."
        ),
        "backends": dict(_ALL_BACKENDS),
        "lifecycle": "long_term",
        "expect": {
            "one_of_required": [["document_id"], ["doc_id"]],
            "input_alias_properties": ["doc_id"],
            "output_alias_properties": ["doc_id", "doc_type", "source_path"],
        },
    },
    "get_document_summary": {
        "kind": "document_metadata_compat_alias",
        "returns": "Same handler, schema and output as get_document.",
        "canonical_params": ["doc_id"],
        "compat_aliases": [
            {"surface": "tool_name", "alias": "get_document_summary",
             "canonical": "get_document",
             "phase_out": "compatibility window (>= one release)"},
            {"surface": "output", "alias": "doc_id",
             "canonical": "document_id", "phase_out": "compatibility window"},
            {"surface": "output", "alias": "doc_type",
             "canonical": "document_type", "phase_out": "compatibility window"},
            {"surface": "output", "alias": "source_path",
             "canonical": "source", "phase_out": "compatibility window"},
        ],
        "pagination": None,
        "limits": "doc_id required; additionalProperties rejected.",
        "errors": [
            {"condition": "doc_id missing/empty",
             "surface": "tool result is_error=true",
             "message": (
                 "'document_id' is required and must be a non-empty string")},
            {"condition": "unknown/unauthorized id",
             "surface": "tool result is_error=true",
             "message": "document not found or not accessible"},
        ],
        "collection_authorization": (
            "Identical to get_document (shared implementation)."
        ),
        "backends": dict(_ALL_BACKENDS),
        "lifecycle": "compat_alias",
        "expect": {
            "required": ["doc_id"],
            "input_alias_properties": ["doc_id"],
            "output_alias_properties": ["doc_id", "doc_type", "source_path"],
        },
    },
    "get_document_chunks": {
        "kind": "chunk_pagination",
        "returns": (
            "One stable-ordered page of a document's indexed chunks with "
            "full chunk text, page/section metadata and total/has_next."
        ),
        "canonical_params": ["document_id", "page", "page_size"],
        "compat_aliases": [
            {"surface": "input", "alias": "doc_id",
             "canonical": "document_id", "phase_out": "compatibility window"},
        ],
        "pagination": (
            "1-based page pagination; page>=1, page_size 1..50 "
            "(default 20). Ordering is explicit and independent of the "
            "vector store's natural order (chunk_index -> embedded index "
            "-> chunk id)."
        ),
        "limits": "document_id or doc_id required (oneOf).",
        "errors": [
            {"condition": "document id missing/empty",
             "surface": "tool result is_error=true",
             "message": (
                 "'document_id' is required and must be a non-empty string")},
            {"condition": "page/page_size not integers",
             "surface": "tool result is_error=true",
             "message": "'page' and 'page_size' must be integers"},
            {"condition": "page_size outside 1..50 or page < 1",
             "surface": "tool result is_error=true",
             "message": "page_size must be between 1 and 50 / page must be >= 1"},
            {"condition": "unknown/unauthorized id",
             "surface": "tool result is_error=true",
             "message": "document not found or not accessible"},
        ],
        "collection_authorization": (
            "Same UUID resolution + collection re-check as get_document, "
            "performed before chunk reads."
        ),
        "backends": dict(_ALL_BACKENDS),
        "lifecycle": "long_term",
        "expect": {
            "one_of_required": [["document_id"], ["doc_id"]],
            "input_alias_properties": ["doc_id"],
            "page_size": {"min": 1, "max": 50, "default": 20},
        },
    },
}


def build_registry() -> Any:
    """Build a fresh handler with every production tool registered."""
    from src.mcp_server.protocol_handler import ProtocolHandler
    from src.mcp_server.server import _register_default_tools

    handler = ProtocolHandler(server_name="contract-inventory")
    _register_default_tools(handler)
    return handler


def _check_expect(name: str, registration: Any, curated: dict[str, Any]) -> None:
    """Cross-validate curated alias/limit claims against the live schema.

    Raises AssertionError when the registered schema no longer matches what
    the curated inventory tells clients — regeneration must not paper over
    a real contract drift.
    """
    expect = curated.get("expect", {})
    in_props = set(registration.input_schema.get("properties", {}))
    out_props = set((registration.output_schema or {}).get("properties", {}))

    for key in expect.get("required", []):
        assert key in registration.input_schema.get("required", []), (
            f"{name}: curated contract requires input prop {key!r}"
        )
    if "one_of_required" in expect:
        one_of = registration.input_schema.get("oneOf", [])
        actual = sorted(
            ",".join(sorted(clause.get("required", []))) for clause in one_of
        )
        wanted = sorted(
            ",".join(sorted(clause)) for clause in expect["one_of_required"]
        )
        assert actual == wanted, (
            f"{name}: oneOf drift: {actual!r} != {wanted!r}"
        )
    for key in expect.get("input_alias_properties", []):
        assert key in in_props, f"{name}: input alias {key!r} missing in schema"
    for key in expect.get("output_alias_properties", []):
        assert key in out_props, f"{name}: output alias {key!r} missing in schema"
    if "max_query_length" in expect:
        q = registration.input_schema["properties"]["query"]
        assert q.get("maxLength") == expect["max_query_length"], (
            f"{name}: query.maxLength drift"
        )
    for param_key in ("top_k", "page_size"):
        if param_key in expect:
            prop = registration.input_schema["properties"][param_key]
            want = expect[param_key]
            assert prop.get("minimum") == want["min"], f"{name}: {param_key} min"
            assert prop.get("maximum") == want["max"], f"{name}: {param_key} max"
            assert prop.get("default") == want["default"], (
                f"{name}: {param_key} default"
            )


def build_inventory() -> dict[str, Any]:
    """Build the complete inventory dict from the live registry."""
    handler = build_registry()
    registered = set(handler.list_names())
    curated = set(_CURATED)
    if registered != curated:
        missing = sorted(curated - registered)
        extra = sorted(registered - curated)
        raise AssertionError(
            "MCP tool registry and contract inventory diverged: "
            f"curated-but-unregistered={missing}, registered-but-undocumented={extra}. "
            "Update the tool surface AND this inventory deliberately."
        )

    tools: dict[str, Any] = {}
    for name in sorted(registered):
        reg = handler.get(name)
        curated_entry = _CURATED[name]
        _check_expect(name, reg, curated_entry)
        tools[name] = {
            "name": reg.name,
            "description": reg.description,
            "kind": curated_entry["kind"],
            "returns": curated_entry["returns"],
            "input_schema": reg.input_schema,
            "output_schema": reg.output_schema,
            "canonical_params": curated_entry["canonical_params"],
            "compat_aliases": curated_entry["compat_aliases"],
            "pagination": curated_entry["pagination"],
            "limits": curated_entry["limits"],
            "errors": curated_entry["errors"],
            "collection_authorization": curated_entry["collection_authorization"],
            "backends": curated_entry["backends"],
            "lifecycle": curated_entry["lifecycle"],
        }

    return {
        "contract": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "generated_from": (
            "src.mcp_server.server._register_default_tools (live MCP "
            "tools/list registry; shared by stdio and streamable-http)"
        ),
        "read_only": True,
        "tool_count": len(tools),
        "tools": tools,
    }


def dump(inventory: dict[str, Any]) -> str:
    return json.dumps(inventory, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Write JSON here instead of stdout.")
    parser.add_argument(
        "--check", action="store_true",
        help="Compare to the committed fixture and exit non-zero on drift.",
    )
    args = parser.parse_args(argv)

    inventory = build_inventory()
    rendered = dump(inventory)

    if args.check:
        if not FIXTURE_PATH.is_file():
            print(f"missing fixture: {FIXTURE_PATH}", file=sys.stderr)
            return 2
        committed = FIXTURE_PATH.read_text(encoding="utf-8")
        if committed != rendered:
            print(
                "MCP read-only contract inventory drifted from "
                f"{FIXTURE_PATH}. Review the diff; if intentional, regenerate "
                "with MCP_REGENERATE_V1=1 pytest or --output.",
                file=sys.stderr,
            )
            return 1
        print(f"ok: {FIXTURE_PATH} is current ({inventory['tool_count']} tools)")
        return 0

    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
