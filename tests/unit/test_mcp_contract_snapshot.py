"""
Phase 0 baseline contract snapshot (P0.1).

Captures the existing MCP tool surface so later phases can prove
"production behaviour has not changed". Two artefacts:

1. **Tool schemas** — every registered tool's name / description /
   ``input_schema`` / ``output_schema``, as exposed to clients via
   ``tools/list``. Transport-agnostic: the same registry is shared by
   stdio and streamable-HTTP, so one snapshot covers both.

2. **Behaviour samples** — representative *offline* outputs (empty
   result, parameter error, not-found) that need no external model,
   vector DB or upload. These lock the backward-compatible error
   strings and empty-state payloads that Phase 1 / 2 must preserve.

The golden file is committed under ``tests/fixtures/mcp_contract/``.
Regenerating it is explicit and deliberate:

    MCP_REGENERATE_SNAPSHOT=1 .venv/bin/python -m pytest \
        tests/unit/test_mcp_contract_snapshot.py -q

Without that env var the test asserts the live registry still matches
the committed baseline (so an accidental contract change fails loudly).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_contract"
GOLDEN = FIXTURE_DIR / "phase0_tool_schemas.json"

REGENERATE = os.environ.get("MCP_REGENERATE_SNAPSHOT") == "1"

# Tools that must NOT appear anywhere on the read surface (P0.2 cross-check).
_FORBIDDEN_WORDS = (
    "create", "upload", "update", "delete", "import", "chat", "agent",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_handler():
    from src.mcp_server.protocol_handler import ProtocolHandler
    from src.mcp_server.server import _register_default_tools

    handler = ProtocolHandler(server_name="snapshot-test")
    _register_default_tools(handler)
    return handler


def _schema_block(handler):
    return {
        name: {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
            "output_schema": t.output_schema,
        }
        for name, t in sorted(handler._tools.items())
    }


def _behavior_samples(tmp_path) -> dict:
    """Offline, deterministic samples from the existing three tools."""
    import asyncio

    from src.mcp_server.tools import get_document_summary as gds
    from src.mcp_server.tools import list_collections as lc
    from src.mcp_server.tools import query_knowledge_hub as qkh

    def _run(coro):
        return asyncio.run(coro)

    # Empty data dir + an explicit empty collection name → true empty-state
    # (no configured collection to fall back on). Vector counts are stubbed
    # so the sample needs no real Chroma DB.
    settings_path = tmp_path / "empty-settings.yaml"
    settings_path.write_text(
        "vector_store:\n  backend: chroma\n"
        "  persist_path: " + str(tmp_path / "chroma") + "\n"
        "  collection_name: \"\"\n",
        encoding="utf-8",
    )
    with patch.object(lc, "_vector_counts", return_value={}):
        md, structured = _run(lc._list_collections({
            "_data_dir": str(tmp_path / "nope"),
            "_config_path": str(settings_path),
        }))

    # Empty query → known parameter error (CallToolResult.is_error=True).
    qr = _run(qkh._query_knowledge_hub({"query": "  "}))

    # Not-found document → same-shape error (resolve returns None).
    with patch.object(gds, "_resolve_doc", return_value=None):
        gr = _run(gds._get_document_summary({
            "doc_id": "00000000-0000-0000-0000-000000000000",
            "_config_path": str(tmp_path / "no-such.yaml"),
            "_data_dir": str(tmp_path),
        }))

    return {
        "list_collections_empty_state": {
            "markdown_has_no_collections": "No collections found" in md,
            "structured": structured,
        },
        "query_knowledge_hub_empty_query": {
            "is_error": bool(qr.is_error),
            "message": qr.content[0].text,
        },
        "get_document_summary_not_found": {
            "is_error": bool(gr.is_error),
            "message": gr.content[0].text,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_registered_tool_names_are_stable_and_readonly():
    """The read surface is exactly the baseline three tools (before the
    Phase 2/3 additions); none is a write / chat / agent tool."""
    handler = _build_handler()
    names = sorted(handler.list_names())
    assert names == [
        "get_document_summary", "list_collections", "query_knowledge_hub",
    ]
    lowered = " ".join(names).lower()
    for word in _FORBIDDEN_WORDS:
        assert word not in lowered, f"forbidden read-tool name present: {word}"


def test_snapshot_matches_committed_baseline(tmp_path):
    handler = _build_handler()
    live = {
        "tool_schemas": _schema_block(handler),
        "behavior_samples": _behavior_samples(tmp_path),
    }
    if REGENERATE:
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(
            json.dumps(live, indent=2, ensure_ascii=False, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        pytest.skip("snapshot regenerated")
        return
    assert GOLDEN.is_file(), (
        "missing committed golden snapshot; run with "
        "MCP_REGENERATE_SNAPSHOT=1 to create it"
    )
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert live == golden, (
        "MCP tool contract drifted from the committed Phase 0 baseline — "
        "inspect the diff before updating the snapshot."
    )


def test_behavior_samples_have_expected_shape(tmp_path):
    """Independent sanity so the golden snapshot is not a tautology."""
    samples = _behavior_samples(tmp_path)
    assert samples["list_collections_empty_state"]["markdown_has_no_collections"]
    assert samples["list_collections_empty_state"]["structured"] == {
        "n_collections": 0, "collections": [],
    }
    assert samples["query_knowledge_hub_empty_query"]["is_error"]
    assert "query" in samples["query_knowledge_hub_empty_query"]["message"]
    assert samples["get_document_summary_not_found"]["is_error"]
    assert "document not found" in (
        samples["get_document_summary_not_found"]["message"]
    )