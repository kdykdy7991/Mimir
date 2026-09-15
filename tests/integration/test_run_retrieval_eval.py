"""
Integration tests for scripts/run_retrieval_eval.py (Task 01.4).

The real seeded Golden Set indexes (Chroma + BM25 in a temporary data
dir) are driven through the SAME application-layer composition root the
MCP in-process client uses. Tests cover:

* normal hybrid/sparse runs and the full per-case/aggregate report;
* deterministic repeatability on one fixed index;
* rerank requested but unavailable -> degraded, not zero recall;
* bad input -> exit 2; missing data/infra -> exit 1;
* a simulated upstream failure marks cases status=error, which is NEVER
  counted as a miss or as a no-answer result.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import run_retrieval_eval as runner
from scripts.eval_support import CASES_PATH, CORPUS_PATH, seed_fixture_data

ANSWERABLE = 7
TOTAL_CASES = 8


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("golden-eval-data")
    seed_fixture_data(path, corpus_path=CORPUS_PATH)
    return path


def _args(data_dir, **overrides):
    argv = [
        "--data-dir", str(data_dir),
        "--cases", str(CASES_PATH),
        "--mode", overrides.get("mode", "hybrid"),
        "--top-k", str(overrides.get("top_k", 10)),
        "--config", overrides.get("config", "config/settings.yaml"),
        "--profile", overrides.get("profile", "ci"),
    ]
    if overrides.get("rerank"):
        argv.append("--rerank")
    if overrides.get("output"):
        argv += ["--output", str(overrides["output"])]
    if overrides.get("report_md"):
        argv += ["--report-md", str(overrides["report_md"])]
    return runner.parse_args(argv)


def _by_id(report):
    return {c["id"]: c for c in report["cases"]}


# ---------------------------------------------------------------------------
# Normal runs
# ---------------------------------------------------------------------------

def test_hybrid_run_full_report(data_dir):
    report, exit_code = runner.run_evaluation(_args(data_dir))
    assert exit_code == 0
    assert report["status"] == "ok"
    assert report["schema_version"] == "retrieval-eval-v1"
    assert report["case_count"] == TOTAL_CASES
    assert report["error_count"] == 0
    assert report["ks"] == [1, 5, 10]

    summary = report["summary"]
    assert summary["answerable_evaluated"] == ANSWERABLE
    assert summary["recall"]["recall_at_10"] == pytest.approx(1.0)
    assert summary["mrr"] == pytest.approx(1.0)
    assert summary["document_hit_rate"] == 1.0
    assert summary["chunk_hit_rate"] == 1.0
    assert 0.0 <= summary["precision"]["precision_at_5"] <= 1.0
    for key in ("p50", "p95", "mean"):
        assert summary["latency_ms"][key] >= 0.0
    assert summary["avg_returned_chars"] > 0
    assert set(summary["branch_contribution"]) == {
        "dense_candidates_total", "sparse_candidates_total", "returned_by_source",
    }

    cases = _by_id(report)
    case = cases["kw-en-quixbuckle"]
    assert case["status"] == "ok"
    assert case["metrics"]["relevant_ranks"] == [1]
    top = case["actual"][0]
    assert top["rank"] == 1
    assert set(top) == {"rank", "chunk_id", "document_id", "score",
                       "branch", "safe_preview"}
    assert top["chunk_id"] == case["expected_chunk_ids"][0]
    assert top["document_id"] in case["expected_document_ids"]
    for row in case["actual"]:
        assert len(row["safe_preview"]) <= 120
        assert "\n" not in row["safe_preview"]
    assert set(case["branch_counts"]) == {
        "dense_candidates", "sparse_candidates", "fused_candidates",
    }

    # Dense/hybrid have no similarity threshold: the no-answer case is an
    # honest false positive, recorded as such (known behavior, not a fix).
    no_answer = cases["noanswer-florpsnickle"]
    assert no_answer["metrics"]["returned_count"] == 10
    assert summary["no_answer_total"] == 1
    assert summary["no_answer_false_positives"] == 1
    assert summary["no_answer_correct"] == 0
    assert summary["no_answer_accuracy"] == 0.0


def test_sparse_no_answer_is_strictly_empty(data_dir):
    report, exit_code = runner.run_evaluation(_args(data_dir, mode="sparse"))
    assert exit_code == 0
    summary = report["summary"]
    assert summary["recall"]["recall_at_1"] == pytest.approx(1.0)
    assert summary["no_answer_correct"] == 1
    assert summary["no_answer_false_positives"] == 0
    assert summary["no_answer_accuracy"] == 1.0
    no_answer = _by_id(report)["noanswer-florpsnickle"]
    assert no_answer["actual"] == []
    # Tiny corpus: BM25 need not fill top_k; counts come from the cases.
    expected_rows = sum(
        c["metrics"]["returned_count"]
        for c in report["cases"] if not c["expect_no_answer"]
    )
    assert summary["branch_contribution"]["returned_by_source"] == {
        "sparse": expected_rows,
    }


def test_dense_mode_runs_and_reports_dense_branch(data_dir):
    report, exit_code = runner.run_evaluation(_args(data_dir, mode="dense"))
    assert exit_code == 0
    assert report["summary"]["answerable_evaluated"] == ANSWERABLE
    # Hash embeddings: every answerable target is reachable within top-10.
    assert report["summary"]["recall"]["recall_at_10"] == pytest.approx(1.0)
    sources = report["summary"]["branch_contribution"]["returned_by_source"]
    assert set(sources) == {"dense"}


def test_top_k_changes_ks_and_window_size(data_dir):
    report, exit_code = runner.run_evaluation(_args(data_dir, top_k=3))
    assert exit_code == 0
    assert report["ks"] == [1, 3]
    assert report["top_k"] == 3
    assert set(report["summary"]["recall"]) == {"recall_at_1", "recall_at_3"}
    for case in report["cases"]:
        assert len(case["actual"]) <= 3


def test_rerank_unavailable_is_degraded_not_zero_recall(data_dir):
    report, exit_code = runner.run_evaluation(_args(data_dir, rerank=True))
    # This environment has no rerank backend: run completes, flagged.
    assert exit_code == 0
    assert report["status"] == "degraded"
    assert report["rerank"]["requested"] is True
    assert report["rerank"]["applied"] is False
    assert report["rerank"]["status"] == "skipped"
    assert report["rerank"]["reason"]
    assert report["summary"]["recall"]["recall_at_10"] == pytest.approx(1.0)
    assert any("rerank requested but unavailable" in r
               for r in report["degraded_reasons"])


def test_same_index_runs_are_identical(data_dir):
    first, code1 = runner.run_evaluation(_args(data_dir))
    second, code2 = runner.run_evaluation(_args(data_dir))
    assert code1 == code2 == 0

    def ranking(report):
        return {
            c["id"]: [row["chunk_id"] for row in c["actual"]]
            for c in report["cases"]
        }

    assert ranking(first) == ranking(second)
    # Scores are stable too (latencies are intentionally excluded).
    assert [[(r["chunk_id"], r["score"]) for r in c["actual"]]
            for c in first["cases"]] == [
        [(r["chunk_id"], r["score"]) for r in c["actual"]]
        for c in second["cases"]
    ]


def test_main_writes_json_and_markdown(data_dir, tmp_path):
    out_json = tmp_path / "report.json"
    out_md = tmp_path / "report.md"
    code = runner.main([
        "--data-dir", str(data_dir), "--cases", str(CASES_PATH),
        "--mode", "sparse", "--top-k", "10",
        "--output", str(out_json), "--report-md", str(out_md),
    ])
    assert code == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    md = out_md.read_text(encoding="utf-8")
    assert md.startswith("# Retrieval Golden Set Evaluation")
    assert "`sparse`" in md
    assert "kw-en-quixbuckle" in md


# ---------------------------------------------------------------------------
# Bad input -> exit 2
# ---------------------------------------------------------------------------

def test_missing_cases_file_is_usage_error(data_dir):
    args = SimpleNamespace(
        data_dir=str(data_dir), cases="/tmp/does-not-exist-cases.jsonl",
        mode="hybrid", top_k=10, rerank=False,
        config="config/settings.yaml", profile="ci",
    )
    report, exit_code = runner.run_evaluation(args)
    assert exit_code == 2
    assert report["status"] == "usage_error"


def test_malformed_cases_file_is_usage_error(data_dir, tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n", encoding="utf-8")
    args = SimpleNamespace(
        data_dir=str(data_dir), cases=str(bad), mode="hybrid", top_k=10,
        rerank=False, config="config/settings.yaml", profile="ci",
    )
    report, exit_code = runner.run_evaluation(args)
    assert exit_code == 2
    assert report["status"] == "usage_error"


def test_foreign_collection_case_is_usage_error(data_dir, tmp_path):
    # The case schema itself const-pins collection_ids to golden_v1, so a
    # user collection can never be evaluated through this runner.
    case = {
        "schema_version": "retrieval-golden-v1",
        "id": "foreign-collection-case",
        "query": "x",
        "collection_ids": ["some_user_collection"],
        "relevant_document_ids": [],
        "relevant_chunk_ids": [],
        "acceptable_source_locations": [],
        "filters": None,
        "tags": [],
        "category": "no_answer",
        "expect_no_answer": True,
    }
    path = tmp_path / "foreign.jsonl"
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    args = SimpleNamespace(
        data_dir=str(data_dir), cases=str(path), mode="hybrid", top_k=10,
        rerank=False, config="config/settings.yaml", profile="ci",
    )
    report, exit_code = runner.run_evaluation(args)
    assert exit_code == 2
    assert report["status"] == "usage_error"
    assert "golden_v1" in report["error"]


def test_top_k_out_of_range_is_argparse_error():
    with pytest.raises(SystemExit) as exc:
        runner.parse_args(["--top-k", "0"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Infrastructure failures -> exit 1, never counted as retrieval outcomes
# ---------------------------------------------------------------------------

def test_unseeded_data_dir_is_infra_error(tmp_path):
    args = SimpleNamespace(
        data_dir=str(tmp_path / "never-seeded"), cases=str(CASES_PATH),
        mode="hybrid", top_k=10, rerank=False,
        config="config/settings.yaml", profile="ci",
    )
    report, exit_code = runner.run_evaluation(args)
    assert exit_code == 1
    assert report["status"] == "infra_error"
    assert "not seeded" in report["error"]


def test_missing_bm25_index_is_infra_error(tmp_path):
    # A dir with only a manifest is not enough for sparse/hybrid.
    (tmp_path / "build_manifest.json").write_text(json.dumps({
        "embedding_profile": "deterministic-hash-v1",
        "corpus_revision": "x",
    }), encoding="utf-8")
    args = SimpleNamespace(
        data_dir=str(tmp_path), cases=str(CASES_PATH),
        mode="sparse", top_k=10, rerank=False,
        config="config/settings.yaml", profile="ci",
    )
    report, exit_code = runner.run_evaluation(args)
    assert exit_code == 1
    assert report["status"] == "infra_error"
    assert "BM25 index missing" in report["error"]


class _ExplodingService:
    def search(self, *a, **kw):
        raise RuntimeError("simulated embedding provider outage")


def test_upstream_failure_cases_are_errors_not_misses(data_dir, monkeypatch):
    monkeypatch.setattr(
        runner, "build_service",
        lambda path, note: _ExplodingService(),
    )
    report, exit_code = runner.run_evaluation(_args(data_dir))
    assert exit_code == 1
    assert report["status"] == "error"
    assert len(report["errors"]) == TOTAL_CASES
    assert all(c["status"] == "error" for c in report["cases"])
    assert all(c["error_kind"] == "RuntimeError" for c in report["cases"])
    assert all(len(c["error"]) <= 300 for c in report["cases"])

    summary = report["summary"]
    # No case is evaluated: metrics must be None, recall is NEVER zeroed.
    assert summary["answerable_evaluated"] == 0
    assert all(v is None for v in summary["recall"].values())
    assert all(v is None for v in summary["precision"].values())
    assert all(v is None for v in summary["ndcg"].values())
    # The no-answer case failing upstream is not scored as either outcome.
    assert summary["no_answer_total"] == 0
    assert summary["no_answer_correct"] == 0
    assert summary["no_answer_false_positives"] == 0
    assert {e["id"] for e in report["errors"]} == {
        c["id"] for c in report["cases"]
    }
