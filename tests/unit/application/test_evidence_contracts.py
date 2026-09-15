"""
Task 02.1 — transport-neutral evidence contracts.

Covers: EvidenceScores null semantics, EvidenceV1 required/optional
fields and timezone-aware datetimes, SourceLocator path rejection,
EvidenceFilterV1 validation rules, warnings, pagination envelopes,
immutability, deterministic serialization, unknown-field forward
compatibility, and the application-layer dependency boundary (no MCP /
Web / Chroma / BM25 / LLM imports).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.application.contracts import (
    EvidenceFilterV1,
    EvidencePageV1,
    EvidenceScores,
    EvidenceV1,
    SourceLocator,
    TagOperator,
    WarningCode,
    WarningV1,
    to_json,
    to_jsonable,
)
from src.application.contracts.serialization import ContractError

REPO_ROOT = Path(__file__).resolve().parents[3]
TZ = dt.timezone.utc


def _evidence(**overrides) -> EvidenceV1:
    kwargs = dict(
        collection_id="col-1",
        document_id="doc-1",
        chunk_id="doc-1_0001_abcd1234",
        content_type="text",
        source_locator=SourceLocator(kind="page", page=3, heading="Intro"),
        scores=EvidenceScores(fusion=0.42),
        matched_queries=("vector search",),
    )
    kwargs.update(overrides)
    return EvidenceV1(**kwargs)


# ---------------------------------------------------------------------------
# EvidenceScores — null semantics
# ---------------------------------------------------------------------------

def test_scores_unexecuted_stages_serialize_as_null_not_zero():
    payload = to_jsonable(EvidenceScores(fusion=0.42))
    assert payload == {"dense": None, "sparse": None, "fusion": 0.42, "rerank": None}
    assert json.loads(to_json(EvidenceScores())) == {
        "dense": None, "sparse": None, "fusion": None, "rerank": None,
    }


def test_scores_reject_non_finite_and_wrong_type():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ContractError):
            EvidenceScores(dense=bad)
    with pytest.raises(ContractError):
        EvidenceScores(dense="0.5")
    with pytest.raises(ContractError):
        EvidenceScores(dense=True)


def test_scores_from_mapping_ignores_unknown_stage_keys():
    scores = EvidenceScores.from_mapping(
        {"fusion": 1.0, "future_stage": 9.9},
    )
    assert scores.fusion == 1.0
    assert to_jsonable(scores) == {
        "dense": None, "sparse": None, "fusion": 1.0, "rerank": None,
    }


# ---------------------------------------------------------------------------
# SourceLocator
# ---------------------------------------------------------------------------

def test_source_locator_keeps_relative_provenance_only():
    payload = to_jsonable(SourceLocator(kind="page", page=12))
    assert payload == {"kind": "page", "page": 12, "heading": None}


@pytest.mark.parametrize("bad_kind", ["/etc/passwd", "/var/data/db", "C:\\Users\\secret"])
def test_source_locator_rejects_absolute_paths(bad_kind):
    with pytest.raises(ContractError):
        SourceLocator(kind=bad_kind)


@pytest.mark.parametrize("bad_page", [0, -1, True, "2"])
def test_source_locator_rejects_bad_pages(bad_page):
    with pytest.raises(ContractError):
        SourceLocator(kind="page", page=bad_page)


# ---------------------------------------------------------------------------
# EvidenceV1
# ---------------------------------------------------------------------------

def test_evidence_required_fields_and_null_optionals():
    payload = json.loads(to_json(_evidence()))
    for name in (
        "document_version", "chunk_version", "parent_chunk_id", "title",
        "content", "content_preview", "heading_path", "asset_ids", "indexed_at",
    ):
        assert payload[name] is None
    assert payload["matched_queries"] == ["vector search"]
    assert payload["source_locator"] == {"kind": "page", "page": 3, "heading": "Intro"}


def test_evidence_requires_nonempty_identity_and_queries():
    with pytest.raises(ContractError):
        _evidence(collection_id=" ")
    with pytest.raises(ContractError):
        _evidence(chunk_id="")
    with pytest.raises(ContractError):
        _evidence(content_type="")
    with pytest.raises(ContractError):
        _evidence(matched_queries=())
    with pytest.raises(ContractError):
        _evidence(matched_queries=[""])


def test_evidence_accepts_mappings_for_nested_contracts():
    ev = EvidenceV1(
        collection_id="c", document_id="d", chunk_id="k", content_type="text",
        source_locator={"kind": "section", "heading": "H"},
        scores={"dense": 0.1, "fusion": 0.2},
        matched_queries=["q1", "q1", "q2"],
    )
    assert ev.scores.sparse is None
    # string_tuple dedupes deterministically while keeping first order.
    assert ev.matched_queries == ("q1", "q2")
    assert ev.source_locator.kind == "section"


def test_evidence_indexed_at_must_be_timezone_aware():
    with pytest.raises(ContractError):
        _evidence(indexed_at=dt.datetime(2026, 9, 15, 10, 0, 0))
    aware = dt.datetime(2026, 9, 15, 10, 0, 0, tzinfo=TZ)
    payload = to_jsonable(_evidence(indexed_at=aware))
    assert payload["indexed_at"] == "2026-09-15T10:00:00+00:00"


def test_evidence_unknown_optional_fields_ignored_on_read():
    ev = EvidenceV1.from_mapping({
        "collection_id": "c", "document_id": "d", "chunk_id": "k",
        "content_type": "text",
        "source_locator": {"kind": "page"},
        "scores": {},
        "matched_queries": ["q"],
        "future_field": {"anything": True},
    })
    assert isinstance(ev, EvidenceV1)
    assert not hasattr(ev, "future_field")
    # The unknown key is never re-emitted.
    assert "future_field" not in to_jsonable(ev)


def test_contract_instances_are_immutable():
    ev = _evidence()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.chunk_id = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.scores.dense = 1.0  # type: ignore[misc]


def test_serialization_is_deterministic():
    ev = _evidence(asset_ids=("a1", "a2"), heading_path=("Chapter", "Page"))
    assert to_json(ev) == to_json(EvidenceV1.from_mapping(json.loads(to_json(ev))))


def test_warning_detail_is_immutable_mapping():
    warning = WarningV1(code=WarningCode.TRUNCATED, detail={"kept": 2})
    with pytest.raises(TypeError):
        warning.detail["kept"] = 3  # type: ignore[index]


# ---------------------------------------------------------------------------
# EvidenceFilterV1
# ---------------------------------------------------------------------------

def test_filter_missing_fields_mean_no_restriction():
    flt = EvidenceFilterV1()
    payload = to_jsonable(flt)
    assert payload["collection_ids"] is None
    assert payload["tag_ids"] is None
    assert payload["tag_operator"] == "and"
    assert payload["updated_after"] is None


def test_filter_explicit_empty_lists_are_illegal():
    for field_name in (
        "collection_ids", "document_ids", "tag_ids",
        "file_types", "content_types", "source_types",
    ):
        with pytest.raises(ContractError):
            EvidenceFilterV1(**{field_name: []})


def test_filter_dedupes_preserving_first_occurrence_order():
    flt = EvidenceFilterV1(collection_ids=["b", "a", "b", "c", "a"])
    assert flt.collection_ids == ("b", "a", "c")


def test_filter_invalid_tag_operator_rejected():
    with pytest.raises(ContractError):
        EvidenceFilterV1(tag_ids=["t1"], tag_operator="xor")
    flt = EvidenceFilterV1(tag_ids=["t1"], tag_operator="or")
    assert flt.tag_operator is TagOperator.OR


def test_filter_descendants_requires_folder():
    with pytest.raises(ContractError):
        EvidenceFilterV1(include_descendants=True)
    flt = EvidenceFilterV1(folder_id="f1", include_descendants=True)
    assert flt.include_descendants is True


def test_filter_time_bounds_must_be_aware_and_ordered():
    with pytest.raises(ContractError):
        EvidenceFilterV1(
            updated_after=dt.datetime(2026, 1, 1, tzinfo=TZ),
            updated_before=dt.datetime(2025, 1, 1, tzinfo=TZ),
        )
    with pytest.raises(ContractError):
        EvidenceFilterV1(updated_after=dt.datetime(2026, 1, 1))
    flt = EvidenceFilterV1(
        updated_after=dt.datetime(2026, 1, 1, tzinfo=TZ),
        updated_before=dt.datetime(2026, 2, 1, tzinfo=dt.timezone(
            dt.timedelta(hours=8),
        )),
    )
    payload = to_jsonable(flt)
    assert payload["updated_after"].endswith("+00:00")
    assert payload["updated_before"].endswith("+08:00")


def test_filter_from_mapping_round_trip_ignores_unknown():
    flt = EvidenceFilterV1.from_mapping({
        "tag_ids": ["a"], "future_filter_dimension": True,
    })
    assert flt.tag_ids == ("a",)
    assert "future_filter_dimension" not in to_jsonable(flt)


# ---------------------------------------------------------------------------
# Warnings and EvidencePageV1
# ---------------------------------------------------------------------------

def test_warning_codes_json_values_are_stable():
    assert [c.value for c in WarningCode] == [
        "truncated",
        "rerank_degraded",
        "legacy_metadata_missing",
        "partial_collection_failure",
    ]
    payload = to_jsonable(
        WarningV1(code=WarningCode.RERANK_DEGRADED, message="reranker unavailable"),
    )
    assert payload == {
        "code": "rerank_degraded",
        "message": "reranker unavailable",
        "detail": {},
    }


def test_warning_rejects_unknown_code():
    with pytest.raises(ContractError):
        WarningV1(code="not_a_real_code")


def test_page_basic_offset_shape_and_count():
    page = EvidencePageV1(results=(_evidence(),), page=1, page_size=10, has_next=False)
    payload = to_jsonable(page)
    assert payload["returned_count"] == 1
    assert payload["truncated_results"] is False
    assert payload["truncated_characters"] is False
    assert payload["next_cursor"] is None


def test_page_cursor_shape():
    page = EvidencePageV1(
        results=(), cursor="c0", next_cursor="c1", has_next=True,
    )
    payload = to_jsonable(page)
    assert payload["cursor"] == "c0"
    assert payload["next_cursor"] == "c1"
    assert payload["returned_count"] == 0


def test_page_rejects_mixed_pagination_and_half_page_shape():
    with pytest.raises(ContractError):
        EvidencePageV1(results=(), page=1, cursor="c0")
    with pytest.raises(ContractError):
        EvidencePageV1(results=(), page=2)


def test_page_truncation_requires_explicit_truncated_warning():
    with pytest.raises(ContractError):
        EvidencePageV1(results=(_evidence(),), truncated_results=True)
    with pytest.raises(ContractError):
        EvidencePageV1(results=(_evidence(),), truncated_characters=True)
    page = EvidencePageV1(
        results=(_evidence(),),
        truncated_characters=True,
        warnings=(WarningV1(
            code=WarningCode.TRUNCATED,
            message="content shortened to fit the response budget",
        ),),
    )
    codes = [w["code"] for w in to_jsonable(page)["warnings"]]
    assert codes == ["truncated"]


# ---------------------------------------------------------------------------
# Dependency boundary — application contracts stay transport-neutral
# ---------------------------------------------------------------------------

def test_contracts_modules_import_only_stdlib_and_self():
    pkg = REPO_ROOT / "src" / "application" / "contracts"
    banned_prefixes = (
        "mcp", "fastapi", "starlette", "uvicorn", "httpx",
        "chromadb", "pydantic", "openai", "bm25", "rank_bm25",
    )
    for path in pkg.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "src.mcp_server" not in stripped, (path, line)
                assert "src.web_api" not in stripped, (path, line)
                assert "src.libs" not in stripped, (path, line)
                assert "src.core" not in stripped, (path, line)
                root = stripped.split()[1].split(".")[0]
                assert root not in banned_prefixes, (path, line)


def test_importing_contracts_loads_no_transport_or_infra_modules():
    code = (
        "import sys\n"
        "import src.application.contracts  # noqa: F401\n"
        "banned = {\n"
        "    'mcp', 'fastapi', 'starlette', 'uvicorn', 'httpx',\n"
        "    'chromadb', 'pydantic', 'openai', 'src.mcp_server',\n"
        "    'src.web_api', 'src.libs', 'src.core',\n"
        "}\n"
        "leaked = sorted(m for m in sys.modules if m in banned or "
        "any(m.startswith(b + '.') for b in banned))\n"
        "assert not leaked, leaked\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
