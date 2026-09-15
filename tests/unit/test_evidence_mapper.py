"""
Task 02.2 — centralized MCP evidence mapper.

Covers: sensitive-output redaction (absolute paths, secrets,
Authorization headers, tracebacks), legacy byte-compatible query
formatting (structured + markdown + empty shape), honest per-stage
scores (no guessing from the legacy single score), v1 row mapping,
degraded-warning translation, mapper purity (no infra/DB access).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from src.application.contracts import WarningCode
from src.mcp_server.clients.models import (
    Diagnostics,
    EvidenceItem,
    KnowledgeQueryResult,
)
from src.mcp_server.presentation import (
    LEGACY_EMPTY_HINT,
    evidence_v1_from_legacy,
    format_query_result,
    query_structured,
    redact_sensitive,
    safe_preview,
    v1_evidence_row,
    warnings_from_diagnostics,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _item(**overrides) -> EvidenceItem:
    kwargs = dict(
        rank=1,
        chunk_id="doc-1_0001_abcd1234",
        document_id="doc-1",
        title="Intro",
        source="handbook.pdf",
        page=7,
        score=0.3125,
        text="Vector search retrieves chunks by embedding similarity.",
        source_type="fusion",
    )
    kwargs.update(overrides)
    return EvidenceItem(**kwargs)


def _result(*items, degraded=False, reasons=None) -> KnowledgeQueryResult:
    return KnowledgeQueryResult(
        query="vector search",
        collection="kb",
        count=len(items),
        evidence=list(items),
        diagnostics=Diagnostics(
            degraded=degraded, reasons=list(reasons or []), trace_id="tr-1",
        ),
    )


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,redacted", [
    (
        "Authorization: Bearer skdy_mcp_k7G2F4.abc-def_ghi",
        "Authorization: [REDACTED]",
    ),
    (
        "authorization = bearer abc123.def456",
        "authorization = [REDACTED]",
    ),
    (
        "key skdy_mcp_k1.secretvalue in config",
        "key skdy_mcp_[REDACTED] in config",
    ),
    ("api_key=abcdef123456", "api_key=[REDACTED]"),
    ("token: zzz-secret", "token: [REDACTED]"),
    (
        "stored under /Users/alice/docs/private.pdf locally",
        "stored under [path removed] locally",
    ),
    (
        "log path /var/log/app/trace.log was huge",
        "log path [path removed] was huge",
    ),
    (
        r"windows file C:\Users\alice\secret.txt opened",
        "windows file [path removed] opened",
    ),
])
def test_redact_sensitive_strips_credentials_and_paths(raw, redacted):
    assert redact_sensitive(raw) == redacted


def test_redact_sensitive_strips_traceback_block():
    raw = (
        "before\n"
        "Traceback (most recent call last):\n"
        '  File "/home/app/src/x.py", line 12, in boom\n'
        "    raise ValueError('kaboom')\n"
        "ValueError: kaboom\n"
        "after"
    )
    out = redact_sensitive(raw)
    assert "Traceback (most recent call last):" in out
    assert "/home/app/src/x.py" not in out
    assert "kaboom" not in out
    assert "[traceback removed]" in out


def test_redact_preserves_relative_paths_and_urls():
    raw = "see docs/adr/0001.md or https://example.com/a/b for details"
    assert redact_sensitive(raw) == raw


def test_redact_handles_none_and_non_strings():
    assert redact_sensitive(None) == ""
    assert "123" in redact_sensitive(123)


def test_safe_preview_truncates_like_legacy_excerpt():
    text = "x" * 250
    preview = safe_preview(text, 200)
    assert len(preview) == 200
    assert preview.endswith("…")
    assert preview[:-1] == ("x" * 199)
    assert safe_preview("", 200) == ""
    assert safe_preview(None, 200) == ""


def test_safe_preview_redacts_before_truncating():
    text = "Authorization: Bearer leak-token " + "y" * 300
    preview = safe_preview(text, 200)
    assert "leak-token" not in preview


# ---------------------------------------------------------------------------
# Legacy compatibility — structured payload + markdown
# ---------------------------------------------------------------------------

def test_legacy_structured_payload_is_unchanged():
    result = _result(_item())
    _, structured = format_query_result(result)
    assert set(structured) == {
        "query", "collection", "count", "evidence",
        "diagnostics", "n_results", "citations",
    }
    row = structured["evidence"][0]
    assert set(row) == {
        "rank", "chunk_id", "document_id", "title",
        "source", "page", "score", "text",
    }
    assert structured["count"] == structured["n_results"] == 1
    assert structured["citations"][0]["text_excerpt"] == row["text"]
    assert structured["citations"][0]["source_type"] == "fusion"
    assert structured["diagnostics"] == {
        "degraded": False, "reasons": [], "trace_id": "tr-1",
    }


def test_legacy_empty_payload_and_markdown_are_unchanged():
    markdown, structured = format_query_result(_result())
    assert markdown == LEGACY_EMPTY_HINT
    assert structured == {
        "query": "vector search",
        "collection": "kb",
        "count": 0,
        "evidence": [],
        "diagnostics": {
            "degraded": False, "reasons": [], "trace_id": "tr-1",
        },
        "n_results": 0,
        "citations": [],
    }


def test_legacy_markdown_matches_pre_02_2_rendering():
    result = _result(
        _item(rank=1, page=3, score=0.5),
        _item(rank=2, page=None, score=0.123456, source="notes.md"),
    )
    markdown, _ = format_query_result(result)
    assert "**[1] handbook.pdf** (page 3)" in markdown
    assert "## References" in markdown
    assert "[1] `doc-1_0001_abcd1234` — handbook.pdf (p.3, score=0.5000)" in markdown
    assert "[2] `doc-1_0001_abcd1234` — notes.md (n/a, score=0.1235)" in markdown


def test_query_structured_full_text_is_not_modified_by_sanitizer():
    # The legacy structured field keeps full chunk text (compatibility);
    # redaction applies to v1 previews/free-text surfaces only.
    text = "stored at /Users/alice/secret.pdf"
    row = query_structured(_result(_item(text=text)))["evidence"][0]
    assert row["text"] == text


# ---------------------------------------------------------------------------
# v1 mapping — honest scores, no guessing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source_type,stage", [
    ("dense", "dense"),
    ("sparse", "sparse"),
    ("fusion", "fusion"),
    ("rerank", "rerank"),
])
def test_v1_mapping_populates_only_the_real_stage(source_type, stage):
    ev = evidence_v1_from_legacy(
        _item(source_type=source_type, score=0.9),
        collection_id="kb", query="vector search",
    )
    scores = ev.to_dict()["scores"]
    for name in ("dense", "sparse", "fusion", "rerank"):
        if name == stage:
            assert scores[name] == 0.9
        else:
            assert scores[name] is None


def test_v1_mapping_unknown_source_type_keeps_all_scores_null():
    ev = evidence_v1_from_legacy(
        _item(source_type="retrieval", score=0.9),
        collection_id="kb", query="q",
    )
    assert ev.to_dict()["scores"] == {
        "dense": None, "sparse": None, "fusion": None, "rerank": None,
    }


def test_v1_mapping_identity_locator_queries_and_no_fabrication():
    ev = evidence_v1_from_legacy(
        _item(page=None, title=""),
        collection_id="kb", query="vector search",
    )
    payload = v1_evidence_row(ev)
    assert payload["collection_id"] == "kb"
    assert payload["content_type"] == "text"
    assert payload["source_locator"] == {
        "kind": "chunk", "page": None, "heading": None,
    }
    assert payload["matched_queries"] == ["vector search"]
    # Future fields are never guessed from legacy data.
    assert payload["document_version"] is None
    assert payload["chunk_version"] is None
    assert payload["parent_chunk_id"] is None
    assert payload["asset_ids"] is None
    assert payload["indexed_at"] is None
    assert payload["title"] is None
    assert payload["content"] is None
    assert payload["content_preview"].startswith("Vector search")


def test_v1_mapping_preview_is_redacted_and_bounded():
    ev = evidence_v1_from_legacy(
        _item(text="Authorization: Bearer leaked " + "z" * 400),
        collection_id="kb", query="q", preview_chars=120,
    )
    preview = ev.content_preview
    assert "leaked" not in preview
    assert len(preview) == 120
    assert preview.endswith("…")


def test_v1_page_locator_when_page_present():
    ev = evidence_v1_from_legacy(
        _item(page=42), collection_id="kb", query="q",
    )
    assert ev.source_locator.kind == "page"
    assert ev.source_locator.page == 42


# ---------------------------------------------------------------------------
# Diagnostics → warnings
# ---------------------------------------------------------------------------

def test_warnings_empty_when_not_degraded():
    assert warnings_from_diagnostics(Diagnostics(degraded=False)) == ()


def test_warnings_rerank_degraded():
    warnings = warnings_from_diagnostics(Diagnostics(
        degraded=True,
        reasons=["reranker unavailable; retrieval degraded"],
    ))
    assert len(warnings) == 1
    assert warnings[0].code is WarningCode.RERANK_DEGRADED
    payload = warnings[0].to_dict()
    assert payload["code"] == "rerank_degraded"


# ---------------------------------------------------------------------------
# Purity — mapper never reaches into DB/client/SDK layers
# ---------------------------------------------------------------------------

def test_mapper_module_imports_are_contracts_and_stdlib_only():
    import ast

    mapper = REPO_ROOT / "src" / "mcp_server" / "presentation" / "evidence_mapper.py"
    tree = ast.parse(mapper.read_text(encoding="utf-8"))
    allowed_imports = {
        "__future__",
        "re",
        "dataclasses",
        "typing",
        "src.application.contracts",
        # typing-only (TYPE_CHECKING): no runtime dependency on clients.
        "src.mcp_server.clients.models",
    }
    banned_tokens = ("mcp.", "chromadb", "fastapi", "starlette", "openai")
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            assert not any(tok in module for tok in banned_tokens), module
            assert module in allowed_imports, (module, node.lineno)


def test_application_contracts_do_not_import_presentation():
    code = (
        "import sys\n"
        "import src.application.contracts  # noqa: F401\n"
        "assert 'src.mcp_server' not in sys.modules, "
        "'application layer must not depend on the MCP presentation layer'\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
