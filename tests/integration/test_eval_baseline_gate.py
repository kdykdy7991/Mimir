"""
Integration tests for the retrieval baseline regression gate
(Task 01.5).

All four COMMITTED deterministic baselines must pass against a freshly
seeded, independent data directory. Hand-constructed mutations (a
swapped ranking row, a disappeared case, contract drift) must make the
gate exit 1, and an unavailable stack exit 3 (never counted as a
quality regression).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import eval_gate
from scripts.eval_support import CORPUS_PATH, seed_fixture_data

BASELINE_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "retrieval_golden" / "baselines" / "deterministic-hash-v1"
)
BASELINES = ["dense.json", "sparse.json", "hybrid.json", "hybrid-rerank.json"]


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("golden-gate-data")
    seed_fixture_data(path, corpus_path=CORPUS_PATH)
    return path


def _check(data_dir, baseline_path):
    return eval_gate.main([
        "check", "--data-dir", str(data_dir),
        "--baseline", str(baseline_path), "--profile", "ci",
    ])


@pytest.mark.parametrize("name", BASELINES)
def test_committed_baselines_pass_on_fresh_seed(data_dir, name, capsys):
    assert _check(data_dir, BASELINE_DIR / name) == 0
    err = capsys.readouterr().err
    assert "gate passed" in err


def test_hybrid_rerank_baseline_records_environment_block():
    baseline = json.loads((BASELINE_DIR / "hybrid-rerank.json").read_text())
    assert baseline["contract"]["rerank"] == {
        "requested": True, "applied": False, "status": "skipped",
    }
    # Degraded environment is a real, reproducible record — not zeroed
    # metrics: hybrid rankings are fully present.
    for cid, case in baseline["cases"].items():
        if not case["expect_no_answer"]:
            assert case["relevant_ranks"], cid


def test_record_then_check_roundtrip(data_dir, tmp_path, capsys):
    baseline_path = tmp_path / "sparse.json"
    code = eval_gate.main([
        "record", "--data-dir", str(data_dir),
        "--mode", "sparse", "--top-k", "10", "--profile", "ci",
        "--baseline", str(baseline_path),
    ])
    assert code == 0
    assert baseline_path.is_file()
    assert _check(data_dir, baseline_path) == 0


def test_recorded_baseline_is_byte_deterministic(data_dir, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for path in (a, b):
        eval_gate.main([
            "record", "--data-dir", str(data_dir),
            "--mode", "hybrid", "--profile", "ci", "--baseline", str(path),
        ])
    assert a.read_bytes() == b.read_bytes()


def test_hand_constructed_ranking_swap_fails_gate(data_dir, tmp_path, capsys):
    """Replace the baseline rank-1 target id with a cross-case id; the
    real run must expose this as a ranking regression."""
    baseline = json.loads((BASELINE_DIR / "sparse.json").read_text())
    target = baseline["case_order"][0]
    donor = baseline["case_order"][2]
    donor_id = baseline["cases"][donor]["actual_ids"][0]
    original_first = baseline["cases"][target]["actual_ids"][0]
    baseline["cases"][target]["actual_ids"][0] = donor_id
    mutated = tmp_path / "mutated.json"
    mutated.write_text(json.dumps(baseline, indent=2, sort_keys=True))

    assert _check(data_dir, mutated) == 1
    err = capsys.readouterr().err
    assert "FAIL  [ranking]" in err
    assert target in err
    assert original_first in err or donor_id in err


def test_hand_constructed_case_disappearance_fails_gate(
    data_dir, tmp_path, capsys,
):
    baseline = json.loads((BASELINE_DIR / "hybrid.json").read_text())
    removed = baseline["case_order"].pop()
    del baseline["cases"][removed]
    mutated = tmp_path / "missing-case.json"
    mutated.write_text(json.dumps(baseline, indent=2, sort_keys=True))

    assert _check(data_dir, mutated) == 1
    err = capsys.readouterr().err
    assert "FAIL  [case_set]" in err
    assert removed in err


def test_hand_constructed_contract_drift_fails_gate(
    data_dir, tmp_path, capsys,
):
    baseline = json.loads((BASELINE_DIR / "dense.json").read_text())
    baseline["contract"]["corpus_revision"] = "tampered-revision"
    mutated = tmp_path / "contract.json"
    mutated.write_text(json.dumps(baseline, indent=2, sort_keys=True))

    assert _check(data_dir, mutated) == 1
    assert "[contract]" in capsys.readouterr().err


def test_metric_regression_within_tolerance_can_pass(data_dir, tmp_path, capsys):
    baseline = json.loads((BASELINE_DIR / "dense.json").read_text())
    # Claim a slightly BETTER historical recall@1 than the current hash
    # profile produces (~0.571): a 0.03 dip fails under zero tolerance...
    baseline["metrics"]["recall"]["recall_at_1"] = 0.60
    relaxed = tmp_path / "relaxed.json"
    relaxed.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    assert eval_gate.main([
        "check", "--data-dir", str(data_dir),
        "--baseline", str(relaxed), "--profile", "ci",
    ]) == 1
    assert "[metric]" in capsys.readouterr().err
    # ...and passes with an explicit 0.05 tolerance.
    assert eval_gate.main([
        "check", "--data-dir", str(data_dir),
        "--baseline", str(relaxed), "--profile", "ci",
        "--metric-tolerance", "0.05",
    ]) == 0
    assert "gate passed" in capsys.readouterr().err


def test_unseeded_data_dir_is_infra_exit_3(tmp_path, capsys):
    code = eval_gate.main([
        "check", "--data-dir", str(tmp_path / "unseeded"),
        "--baseline", str(BASELINE_DIR / "sparse.json"), "--profile", "ci",
    ])
    assert code == 3
    assert "inconclusive" in capsys.readouterr().err


def test_profile_mismatch_is_usage_error(data_dir, capsys):
    code = eval_gate.main([
        "check", "--data-dir", str(data_dir),
        "--baseline", str(BASELINE_DIR / "sparse.json"), "--profile", "release",
    ])
    assert code == 2
    assert "does not match recorded profile" in capsys.readouterr().err


def test_missing_baseline_file_is_usage_error(data_dir, tmp_path, capsys):
    code = eval_gate.main([
        "check", "--data-dir", str(data_dir),
        "--baseline", str(tmp_path / "nope.json"), "--profile", "ci",
    ])
    assert code == 2
