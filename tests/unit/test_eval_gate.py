"""
Unit tests for the retrieval baseline/regression gate (Task 01.5).

Hand-constructed reports prove the comparator fails on:

* contract incompatibility (mode/top-k/corpus/rerank drift);
* case disappearance and label changes;
* ranking regression (relevant row demoted / removed / row-set drift);
* aggregate metric regression beyond profile tolerance;
* new no-answer false positives beyond allowance;

and that infrastructure errors produce ONLY infra findings (exit-class
3), never metric/recall regressions.
"""

from __future__ import annotations

import copy
import json

import pytest

from scripts.eval_gate import (
    BASELINE_SCHEMA_VERSION,
    Finding,
    PROFILE_TOLERANCES,
    build_baseline,
    compare_reports,
    dump_baseline,
)


def _case(cid, actual_ids, *, no_answer=False, category="exact_keyword",
          status="ok", expected=None, error_kind=None, error=""):
    if expected is None:
        expected = [] if no_answer else [f"{cid}-rel"]
    ranks = [i for i, x in enumerate(actual_ids, 1) if x in set(expected)]
    return {
        "id": cid,
        "category": category,
        "status": status,
        "expect_no_answer": no_answer,
        "expected_chunk_ids": expected,
        "actual": [
            {"rank": i, "chunk_id": x, "document_id": f"doc-{x}",
             "score": 1.0 / i, "branch": "fusion",
             "safe_preview": "synthetic"}
            for i, x in enumerate(actual_ids, 1)
        ],
        "metrics": {
            "relevant_ranks": ranks,
            "returned_count": len(actual_ids),
        },
        **({"error_kind": error_kind, "error": error} if status == "error"
           else {}),
    }


def _report(*cases, mode="hybrid", status="ok", rerank_status="off",
            no_answer_fp=0, no_answer_total=0, mrr=1.0, recall=1.0):
    answerable = [c for c in cases if not c["expect_no_answer"]]
    return {
        "schema_version": "retrieval-eval-v1",
        "status": status,
        "profile": "ci",
        "mode": mode,
        "top_k": 10,
        "ks": [1, 5, 10],
        "collection": "golden_v1",
        "corpus_revision": "2026-09-15.v1",
        "corpus_sha256": "a" * 64,
        "embedding_profile": "deterministic-hash-v1",
        "rerank": {"requested": rerank_status != "off",
                   "applied": rerank_status == "applied",
                   "status": rerank_status},
        "tie_break": "score_desc_then_chunk_id",
        "case_count": len(cases),
        "summary": {
            "answerable_evaluated": len(answerable),
            "recall": {"recall_at_1": recall, "recall_at_5": recall,
                       "recall_at_10": recall},
            "precision": {"precision_at_1": recall, "precision_at_5": recall,
                          "precision_at_10": recall},
            "mrr": mrr,
            "ndcg": {"ndcg_at_1": recall, "ndcg_at_5": recall,
                     "ndcg_at_10": recall},
            "document_hit_rate": recall,
            "chunk_hit_rate": recall,
            "no_answer_total": no_answer_total,
            "no_answer_correct": no_answer_total - no_answer_fp,
            "no_answer_false_positives": no_answer_fp,
            "no_answer_accuracy": (
                (no_answer_total - no_answer_fp) / no_answer_total
                if no_answer_total else None
            ),
            "branch_contribution": {"returned_by_source": {"fusion": 10}},
        },
        "cases": list(cases),
    }


def _baseline_pair(**report_kw):
    base_report = _report(
        _case("c1", ["c1-rel", "x1", "x2"]),
        _case("c2", ["c2-rel", "y1"]),
        _case("na", ["z1", "z2"], no_answer=True,
              category="no_answer"),
        no_answer_fp=1, no_answer_total=1,
    )
    fresh = copy.deepcopy(base_report)
    if report_kw:
        for key, value in report_kw.items():
            if key.startswith("case:"):
                continue
            fresh[key] = value
    return build_baseline(base_report, profile="ci"), base_report, fresh


@pytest.fixture
def baseline_pair():
    return _baseline_pair()


# ---------------------------------------------------------------------------
# Happy path / serialization
# ---------------------------------------------------------------------------

def test_identical_report_passes(baseline_pair):
    baseline, _, fresh = baseline_pair
    assert compare_reports(baseline, fresh) == []


def test_baseline_serialization_is_byte_deterministic():
    report = _report(_case("c1", ["c1-rel"]))
    a = dump_baseline(build_baseline(report, profile="ci"))
    b = dump_baseline(build_baseline(copy.deepcopy(report), profile="ci"))
    assert a == b
    parsed = json.loads(a)
    assert parsed["baseline_schema_version"] == BASELINE_SCHEMA_VERSION
    # No host/time leakage; latencies and text never enter a baseline.
    assert "latency" not in a and "preview" not in a
    assert sorted(parsed.keys())  # sort_keys=True


def test_record_refuses_error_report():
    broken = _report(
        _case("c1", [], status="error", error_kind="RuntimeError",
              error="down"),
        status="error",
    )
    with pytest.raises(ValueError):
        build_baseline(broken, profile="ci")


# ---------------------------------------------------------------------------
# Contract drift
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mutation", [
    {"mode": "dense"},
    {"top_k": 5},
    {"corpus_revision": "2026-09-16.v1"},
    {"embedding_profile": "some-real-model"},
    {"tie_break": "other"},
    {"rerank": {"requested": True, "applied": False, "status": "skipped"}},
])
def test_contract_drift_is_detected(baseline_pair, mutation):
    baseline, _, fresh = baseline_pair
    fresh.update(mutation)
    findings = compare_reports(baseline, fresh)
    assert findings and all(f.kind == "contract" for f in findings)


def test_corpus_sha256_alone_does_not_gate_but_revision_does(baseline_pair):
    baseline, _, fresh = baseline_pair
    fresh["corpus_sha256"] = "b" * 64
    assert compare_reports(baseline, fresh) == []
    fresh["corpus_revision"] = "other"
    kinds = {f.kind for f in compare_reports(baseline, fresh)}
    assert "contract" in kinds


# ---------------------------------------------------------------------------
# Case set / labels
# ---------------------------------------------------------------------------

def test_case_disappearance_is_detected(baseline_pair):
    baseline, _, fresh = baseline_pair
    fresh["cases"] = [c for c in fresh["cases"] if c["id"] != "c2"]
    findings = compare_reports(baseline, fresh)
    assert any(f.kind == "case_set" and "missing" in f.message
               and "c2" in f.message for f in findings)
    # Contract findings must not mask a case-set event.
    assert all(f.kind == "case_set" for f in findings)


def test_relevance_label_change_is_detected(baseline_pair):
    baseline, _, fresh = baseline_pair
    fresh["cases"][0]["expected_chunk_ids"] = ["x1"]
    findings = compare_reports(baseline, fresh)
    assert any(f.kind == "case_set" and "expected_chunk_ids" in f.message
               for f in findings)


# ---------------------------------------------------------------------------
# Ranking regressions
# ---------------------------------------------------------------------------

def test_relevant_row_demotion_is_ranking_regression(baseline_pair):
    baseline, _, fresh = baseline_pair
    fresh["cases"][0]["actual"] = [
        dict(row, rank=i) for i, row in enumerate(
            [fresh["cases"][0]["actual"][1], fresh["cases"][0]["actual"][0],
             fresh["cases"][0]["actual"][2]], start=1)
    ]
    fresh["cases"][0]["metrics"]["relevant_ranks"] = [2]
    findings = compare_reports(baseline, fresh)
    assert any(f.kind == "ranking" and "rank 2" in f.message
               for f in findings)


def test_target_removal_is_ranking_regression(baseline_pair):
    baseline, _, fresh = baseline_pair
    fresh["cases"][0]["actual"] = [
        {"rank": 1, "chunk_id": "x1", "document_id": "d", "score": 0.9,
         "branch": "fusion", "safe_preview": ""},
        {"rank": 2, "chunk_id": "x2", "document_id": "d", "score": 0.8,
         "branch": "fusion", "safe_preview": ""},
    ]
    fresh["cases"][0]["metrics"]["relevant_ranks"] = []
    findings = compare_reports(baseline, fresh)
    assert any(f.kind == "ranking" and "no longer" in f.message
               for f in findings)


def test_significant_prefix_change_even_if_rank_preserved():
    # Relevant chunk stable at rank 2, but rank 1 identity changes:
    # the significant prefix (positions 1..last-relevant) must gate this.
    base = _report(_case("c1", ["x0", "c1-rel", "x1"]))
    baseline = build_baseline(base, profile="ci")
    fresh = copy.deepcopy(base)
    fresh["cases"][0]["actual"][0]["chunk_id"] = "x9"
    findings = compare_reports(baseline, fresh)
    assert any(f.kind == "ranking" and "significant prefix" in f.message
               for f in findings)


def test_tail_rows_below_last_relevant_rank_are_not_gated():
    # Near-tie zero-signal HNSW rows below the last relevant position can
    # vary between independently built indexes without any metric impact.
    base = _report(_case("c1", ["c1-rel", "x1", "x2"]))
    baseline = build_baseline(base, profile="ci")
    fresh = copy.deepcopy(base)
    fresh["cases"][0]["actual"][2]["chunk_id"] = "x9"
    findings = compare_reports(baseline, fresh)
    # Aggregate recall/precision/ndcg@K are unchanged here too.
    assert not [f for f in findings if f.kind == "ranking"]


def test_release_profile_tolerates_slack_and_set_change():
    base = _report(_case("c1", ["c1-rel", "x1", "x2"]))
    baseline = build_baseline(base, profile="release")
    fresh = copy.deepcopy(base)
    fresh["cases"][0]["actual"] = [
        {"rank": 1, "chunk_id": "x1", "document_id": "d", "score": 0.9,
         "branch": "fusion", "safe_preview": ""},
        {"rank": 2, "chunk_id": "c1-rel", "document_id": "d", "score": 0.8,
         "branch": "fusion", "safe_preview": ""},
        {"rank": 3, "chunk_id": "x9", "document_id": "d", "score": 0.7,
         "branch": "fusion", "safe_preview": ""},
    ]
    fresh["cases"][0]["metrics"]["relevant_ranks"] = [2]
    findings = compare_reports(baseline, fresh)
    assert not [f for f in findings if f.kind == "ranking"]
    assert PROFILE_TOLERANCES["release"]["rank_slack"] == 2
    assert PROFILE_TOLERANCES["release"]["result_set_strict"] is False


# ---------------------------------------------------------------------------
# Metric regressions
# ---------------------------------------------------------------------------

def test_metric_regression_beyond_zero_ci_tolerance(baseline_pair):
    baseline, _, fresh = baseline_pair
    fresh["summary"]["mrr"] = 0.5
    findings = compare_reports(baseline, fresh)
    assert any(f.kind == "metric" and "mrr" in f.message for f in findings)


def test_metric_dip_within_overridden_tolerance_passes(baseline_pair):
    baseline, _, fresh = baseline_pair
    fresh["summary"]["mrr"] = 0.99
    findings = compare_reports(
        baseline, fresh,
        tolerance_overrides={"metric_tolerance": 0.02},
    )
    assert findings == []


# ---------------------------------------------------------------------------
# No-answer
# ---------------------------------------------------------------------------

def test_new_no_answer_false_positive_fails(baseline_pair):
    baseline, base_report, _ = baseline_pair
    # Baseline records the hybrid FP (1); one MORE must fail.
    fresh = copy.deepcopy(base_report)
    na = next(c for c in fresh["cases"] if c["expect_no_answer"])
    na["actual"].append({"rank": 3, "chunk_id": "z3", "document_id": "d",
                         "score": 0.1, "branch": "dense", "safe_preview": ""})
    na["metrics"]["returned_count"] = 3
    fresh["summary"]["no_answer_false_positives"] = 2
    fresh["summary"]["no_answer_correct"] = 0
    fresh["summary"]["no_answer_accuracy"] = 0.0
    findings = compare_reports(baseline, fresh)
    assert any(f.kind == "no_answer" and "false positives" in f.message
               for f in findings)


def test_fp_allowance_override_passes(baseline_pair):
    baseline, base_report, _ = baseline_pair
    fresh = copy.deepcopy(base_report)
    na = next(c for c in fresh["cases"] if c["expect_no_answer"])
    na["actual"].append({"rank": 3, "chunk_id": "z3", "document_id": "d",
                         "score": 0.1, "branch": "dense", "safe_preview": ""})
    na["metrics"]["returned_count"] = 3
    fresh["summary"]["no_answer_false_positives"] = 2
    fresh["summary"]["no_answer_correct"] = 0
    fresh["summary"]["no_answer_accuracy"] = 0.0
    findings = compare_reports(
        baseline, fresh,
        tolerance_overrides={"no_answer_fp_allowance": 1},
    )
    assert not [f for f in findings if f.kind == "no_answer"]


# ---------------------------------------------------------------------------
# Infrastructure separation — the core safety semantic
# ---------------------------------------------------------------------------

def test_infra_errors_short_circuit_without_metric_findings(baseline_pair):
    baseline, _, _ = baseline_pair
    broken = _report(
        _case("c1", [], status="error", error_kind="ConnectionError",
              error="provider down"),
        _case("na", [], no_answer=True, category="no_answer",
              status="error", error_kind="ConnectionError"),
        status="error", mrr=0.0, recall=0.0,
    )
    findings = compare_reports(baseline, broken)
    assert findings, "infra must produce findings"
    assert all(isinstance(f, Finding) and f.kind == "infra"
               for f in findings)
    assert len(findings) == 2
    assert not [f for f in findings if f.kind in
                ("metric", "ranking", "no_answer")]
