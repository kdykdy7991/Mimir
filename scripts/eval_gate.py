#!/usr/bin/env python3
"""
Retrieval baseline recorder and regression gate (Task 01.5).

Two subcommands, both driving the SAME application-layer evaluation as
``scripts/run_retrieval_eval.py`` (no Web/MCP network):

* ``record`` — run one mode (dense/sparse/hybrid[/+rerank]) against the
  seeded Golden Set and write a normalized baseline: contract identity,
  per-case expected ids and observed ranking, aggregate metrics. The
  serialization is byte-deterministic (no timestamps/hostnames) and
  excludes latency, score floats and chunk text.
* ``check`` — run the exact configuration recorded in the baseline and
  fail on:
    - contract incompatibility (corpus/profile/mode/top-k/rerank drift);
    - case disappearance or label change;
    - per-case ranking regression beyond the profile's rank slack;
    - Recall@K/Precision@K/MRR/nDCG/hit-rate regression beyond tolerance;
    - new no-answer false positives beyond the allowance;
    - infrastructure errors — reported SEPARATELY (exit 3), never as
      zero-recall misses (exit 1).

Exit codes: 0 pass / 1 regression / 2 usage / 3 infrastructure failure.

Profiles
--------
``ci`` (default, deterministic-hash embedding): zero tolerances, rank
slack 0, strict result-set equality. ``release`` (real-model profiles
recorded with explicit commands): 0.05 metric tolerance, rank slack 2,
non-strict set comparison, no-answer FP allowance 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import run_retrieval_eval as eval_runner

BASELINE_SCHEMA_VERSION = "retrieval-baseline-v1"

PROFILE_TOLERANCES: dict[str, dict[str, Any]] = {
    "ci": {
        "metric_tolerance": 0.0,
        "rank_slack": 0,
        "result_set_strict": True,
        "no_answer_fp_allowance": 0,
    },
    "release": {
        "metric_tolerance": 0.05,
        "rank_slack": 2,
        "result_set_strict": False,
        "no_answer_fp_allowance": 0,
    },
}

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_USAGE = 2
EXIT_INFRA = 3

_METRIC_FAMILIES = ("recall", "precision", "ndcg")
_SCALAR_METRICS = ("mrr", "document_hit_rate", "chunk_hit_rate",
                   "no_answer_accuracy")


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    kind: str           # contract | case_set | ranking | metric | no_answer | infra
    message: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message}


# ---------------------------------------------------------------------------
# Baseline construction
# ---------------------------------------------------------------------------

def _contract_of(report: dict) -> dict:
    return {
        "eval_schema": report["schema_version"],
        "corpus_revision": report["corpus_revision"],
        "corpus_sha256": report["corpus_sha256"],
        "embedding_profile": report["embedding_profile"],
        "collection": report["collection"],
        "mode": report["mode"],
        "top_k": report["top_k"],
        "ks": report["ks"],
        "tie_break": report["tie_break"],
        "rerank": {
            "requested": report["rerank"]["requested"],
            "applied": report["rerank"]["applied"],
            "status": report["rerank"]["status"],
            **({"backend": report["rerank"]["backend"]}
               if report["rerank"].get("backend") else {}),
        },
    }


def _case_fingerprint(case: dict) -> dict:
    return {
        "category": case["category"],
        "expect_no_answer": case["expect_no_answer"],
        "status": case["status"],
        "expected_chunk_ids": list(case.get("expected_chunk_ids", [])),
        "actual_ids": [row["chunk_id"] for row in case.get("actual", [])],
        "relevant_ranks": list(case.get("metrics", {}).get(
            "relevant_ranks", [])),
        "returned_count": case.get("metrics", {}).get("returned_count", 0),
        "branches": [row["branch"] for row in case.get("actual", [])],
    }


def _metrics_of(report: dict) -> dict:
    summary = report["summary"]
    return {
        "recall": dict(summary["recall"]),
        "precision": dict(summary["precision"]),
        "ndcg": dict(summary["ndcg"]),
        "mrr": summary["mrr"],
        "document_hit_rate": summary["document_hit_rate"],
        "chunk_hit_rate": summary["chunk_hit_rate"],
        "no_answer_total": summary["no_answer_total"],
        "no_answer_correct": summary["no_answer_correct"],
        "no_answer_false_positives": summary["no_answer_false_positives"],
        "no_answer_accuracy": summary["no_answer_accuracy"],
        "branch_contribution": summary["branch_contribution"],
    }


def build_baseline(report: dict, *, profile: str) -> dict:
    if report["status"] == "error":
        raise ValueError("cannot record a baseline with error-status cases")
    tolerances = dict(PROFILE_TOLERANCES[profile])
    return {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "profile": profile,
        "tolerances": tolerances,
        "contract": _contract_of(report),
        "case_order": [c["id"] for c in report["cases"]],
        "cases": {c["id"]: _case_fingerprint(case=c) for c in report["cases"]},
        "metrics": _metrics_of(report),
    }


def dump_baseline(baseline: dict) -> str:
    return json.dumps(
        baseline, indent=2, ensure_ascii=False, sort_keys=True,
    ) + "\n"


# ---------------------------------------------------------------------------
# Comparison (pure)
# ---------------------------------------------------------------------------

def compare_reports(
    baseline: dict,
    report: dict,
    *,
    tolerance_overrides: dict[str, Any] | None = None,
) -> list[Finding]:
    """Return findings (empty list = pass)."""
    tol = dict(baseline.get("tolerances") or PROFILE_TOLERANCES["ci"])
    if tolerance_overrides:
        tol.update(tolerance_overrides)

    findings: list[Finding] = []

    # 1) Infrastructure failures first — they invalidate quality verdicts.
    error_cases = [c for c in report["cases"] if c["status"] == "error"]
    if error_cases or report["status"] == "error":
        for case in error_cases:
            findings.append(Finding(
                "infra",
                f"{case['id']}: retrieval raised {case.get('error_kind')}: "
                f"{case.get('error', '')[:160]}",
            ))
        return findings  # stop: infra must never be read as a quality miss

    # 2) Contract identity.
    fresh_contract = _contract_of(report)
    for key, old_value in baseline["contract"].items():
        if key == "corpus_sha256":
            continue  # recorded for traceability; corpus_revision gates content
        new_value = fresh_contract.get(key)
        if old_value != new_value:
            findings.append(Finding(
                "contract",
                f"{key}: baseline {old_value!r} != current {new_value!r}",
            ))
    if baseline.get("baseline_schema_version") != BASELINE_SCHEMA_VERSION:
        findings.append(Finding(
            "contract",
            f"baseline schema {baseline.get('baseline_schema_version')!r} "
            f"!= {BASELINE_SCHEMA_VERSION!r}",
        ))
    if findings:
        # A different configuration makes ranking/metric diffs meaningless.
        return findings

    # 3) Case set + labels (a disappeared/relabelled case is a gate event).
    fresh_order = [c["id"] for c in report["cases"]]
    if fresh_order != baseline["case_order"]:
        missing = sorted(set(baseline["case_order"]) - set(fresh_order))
        added = sorted(set(fresh_order) - set(baseline["case_order"]))
        findings.append(Finding(
            "case_set",
            f"case set/order drift; missing={missing or '-'} "
            f"added={added or '-'}",
        ))
    fresh_cases = {c["id"]: c for c in report["cases"]}
    for cid, old in baseline["cases"].items():
        new = fresh_cases.get(cid)
        if new is None:
            continue  # already reported as missing above
        fp = _case_fingerprint(new)
        for label_key in ("category", "expect_no_answer", "expected_chunk_ids"):
            if old[label_key] != fp[label_key]:
                findings.append(Finding(
                    "case_set",
                    f"{cid}: {label_key} changed "
                    f"{old[label_key]!r} -> {fp[label_key]!r}",
                ))
    if findings:
        return findings

    # 4) Per-case ranking. The "significant prefix" is positions 1..last
    #    relevant rank in the baseline: rows below it cannot affect
    #    Recall/MRR/nDCG and, for dense/HNSW, contain near-tie zero-signal
    #    rows whose membership varies between independently built indexes.
    #    Tail quality is still bounded by the aggregate Precision@K gate.
    rank_slack = int(tol["rank_slack"])
    strict_set = bool(tol["result_set_strict"])
    for cid, old in baseline["cases"].items():
        new = fresh_cases[cid]
        if new["expect_no_answer"]:
            continue
        old_ids = old["actual_ids"]
        new_ids = [r["chunk_id"] for r in new["actual"]]
        expected = set(old["expected_chunk_ids"])
        new_ranks = [
            i for i, ident in enumerate(new_ids, start=1)
            if ident in expected
        ]
        if not new_ranks:
            findings.append(Finding(
                "ranking",
                f"{cid}: expected chunk no longer in top-{len(new_ids)} "
                f"(baseline ranks {old['relevant_ranks']})",
            ))
            continue
        if strict_set:
            cutoff = max(old["relevant_ranks"], default=0)
            if old_ids[:cutoff] != new_ids[:cutoff]:
                findings.append(Finding(
                    "ranking",
                    f"{cid}: significant prefix (ranks 1..{cutoff}) changed "
                    f"{old_ids[:cutoff]} -> {new_ids[:cutoff]}",
                ))
        base_first = old["relevant_ranks"][0] if old["relevant_ranks"] else None
        if base_first is not None and new_ranks[0] > base_first + rank_slack:
            findings.append(Finding(
                "ranking",
                f"{cid}: first relevant rank {new_ranks[0]} regressed beyond "
                f"baseline {base_first} + slack {rank_slack}",
            ))

    # 5) Aggregate metrics.
    fresh_metrics = _metrics_of(report)
    tolerance = float(tol["metric_tolerance"])
    for family in _METRIC_FAMILIES:
        for key, old_value in baseline["metrics"][family].items():
            new_value = fresh_metrics[family][key]
            if not _within(old_value, new_value, tolerance):
                findings.append(Finding(
                    "metric",
                    f"{key}: {old_value:.4f} -> {new_value:.4f} "
                    f"(tolerance {tolerance})",
                ))
    for key in _SCALAR_METRICS:
        old_value = baseline["metrics"][key]
        new_value = fresh_metrics[key]
        if old_value is None or new_value is None:
            if old_value != new_value:
                findings.append(Finding(
                    "metric", f"{key}: {old_value!r} -> {new_value!r}",
                ))
            continue
        if not _within(old_value, new_value, tolerance):
            findings.append(Finding(
                "metric",
                f"{key}: {old_value:.4f} -> {new_value:.4f} "
                f"(tolerance {tolerance})",
            ))

    # 6) No-answer behavior. Absolute FP allowance dominates tolerance.
    base_fp = baseline["metrics"]["no_answer_false_positives"]
    new_fp = fresh_metrics["no_answer_false_positives"]
    allowance = int(tol["no_answer_fp_allowance"])
    if new_fp > base_fp + allowance:
        findings.append(Finding(
            "no_answer",
            f"no-answer false positives {base_fp} -> {new_fp} "
            f"(allowance +{allowance})",
        ))
    base_acc = baseline["metrics"]["no_answer_accuracy"]
    new_acc = fresh_metrics["no_answer_accuracy"]
    if base_acc is not None and new_acc is not None:
        if not _within(base_acc, new_acc, tolerance):
            findings.append(Finding(
                "no_answer",
                f"no-answer accuracy {base_acc:.4f} -> {new_acc:.4f} "
                f"(tolerance {tolerance})",
            ))

    return findings


def _within(old: float | None, new: float | None, tolerance: float) -> bool:
    if old is None or new is None:
        return old == new
    return new + 1e-12 >= old - tolerance


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_args(ns: argparse.Namespace) -> argparse.Namespace:
    """check: run configuration is taken from the baseline contract, so
    the gate can't be passed by silently changing mode/top-k/rerank."""
    return argparse.Namespace(
        data_dir=ns.data_dir,
        cases=ns.cases,
        mode=ns.contract_mode,
        top_k=ns.contract_top_k,
        rerank=ns.contract_rerank,
        config=ns.config,
        profile=ns.profile,
    )


def _cmd_record(ns: argparse.Namespace) -> int:
    args = argparse.Namespace(
        data_dir=ns.data_dir, cases=ns.cases, mode=ns.mode, top_k=ns.top_k,
        rerank=ns.rerank, config=ns.config, profile=ns.profile,
    )
    report, exit_code = eval_runner.run_evaluation(args)
    if exit_code == eval_runner.EXIT_INFRA:
        print(f"infra failure: {report.get('error', '')}", file=sys.stderr)
        return EXIT_INFRA
    if exit_code == eval_runner.EXIT_USAGE:
        print(f"usage error: {report.get('error', '')}", file=sys.stderr)
        return EXIT_USAGE
    if exit_code != eval_runner.EXIT_OK:
        print(f"infra/skip failure: {report.get('error', report['status'])}",
              file=sys.stderr)
        return EXIT_INFRA
    if report["status"] == "error":
        print("refusing to record: cases have infrastructure errors",
              file=sys.stderr)
        return EXIT_INFRA
    baseline = build_baseline(report, profile=ns.profile)
    recorded_overrides: list[str] = []
    if ns.metric_tolerance is not None:
        baseline["tolerances"]["metric_tolerance"] = ns.metric_tolerance
        recorded_overrides.append(f"metric_tolerance={ns.metric_tolerance}")
    if ns.rank_slack is not None:
        baseline["tolerances"]["rank_slack"] = ns.rank_slack
        recorded_overrides.append(f"rank_slack={ns.rank_slack}")
    if ns.fp_allowance is not None:
        baseline["tolerances"]["no_answer_fp_allowance"] = ns.fp_allowance
        recorded_overrides.append(f"fp_allowance={ns.fp_allowance}")
    if ns.allow_set_drift:
        baseline["tolerances"]["result_set_strict"] = False
        recorded_overrides.append("result_set_strict=false")
    if recorded_overrides:
        baseline["tolerance_rationale"] = ns.tolerance_rationale or (
            "recorded with explicit CLI override; see baselines README"
        )
    path = Path(ns.baseline)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_baseline(baseline), encoding="utf-8")
    note = (
        f" (rerank={baseline['contract']['rerank']['status']})"
        if ns.rerank else ""
    )
    suffix = f" overrides=[{', '.join(recorded_overrides)}]" if recorded_overrides else ""
    print(f"recorded {path}: mode={ns.mode} profile={ns.profile}{note}{suffix}",
          file=sys.stderr)
    return EXIT_OK


def _cmd_check(ns: argparse.Namespace) -> int:
    baseline_path = Path(ns.baseline)
    if not baseline_path.is_file():
        print(f"baseline not found: {baseline_path}", file=sys.stderr)
        return EXIT_USAGE
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline.get("profile") != ns.profile:
        print(
            f"--profile {ns.profile} does not match recorded profile "
            f"{baseline.get('profile')}", file=sys.stderr,
        )
        return EXIT_USAGE
    ns.contract_mode = baseline["contract"]["mode"]
    ns.contract_top_k = baseline["contract"]["top_k"]
    ns.contract_rerank = baseline["contract"]["rerank"]["requested"]

    report, exit_code = eval_runner.run_evaluation(_run_args(ns))
    if exit_code == eval_runner.EXIT_USAGE:
        print(f"usage error: {report.get('error', '')}", file=sys.stderr)
        return EXIT_USAGE
    if exit_code != eval_runner.EXIT_OK:
        print(
            f"infra/skip failure ({report.get('status')}): "
            f"{report.get('error', '')}", file=sys.stderr,
        )
        print("gate inconclusive: infrastructure failure (not a regression)",
              file=sys.stderr)
        return EXIT_INFRA
    overrides: dict[str, Any] = {}
    if ns.rank_slack is not None:
        overrides["rank_slack"] = ns.rank_slack
    if ns.metric_tolerance is not None:
        overrides["metric_tolerance"] = ns.metric_tolerance
    findings = compare_reports(baseline, report,
                               tolerance_overrides=overrides or None)
    infra = [f for f in findings if f.kind == "infra"]
    if infra or report.get("status") == "error":
        for f in infra:
            print(f"INFRA  {f.message}", file=sys.stderr)
        print("gate inconclusive: infrastructure failure (not a regression)",
              file=sys.stderr)
        return EXIT_INFRA
    if findings:
        for f in findings:
            print(f"FAIL  [{f.kind}] {f.message}", file=sys.stderr)
        print(
            f"gate FAILED: {len(findings)} finding(s) vs baseline "
            f"{baseline_path}", file=sys.stderr,
        )
        return EXIT_REGRESSION
    print(f"gate passed against {baseline_path}", file=sys.stderr)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--data-dir", default=str(
            __import__("scripts.eval_support", fromlist=["DEFAULT_DATA_DIR"])
            .DEFAULT_DATA_DIR))
        p.add_argument("--cases", default=str(
            __import__("scripts.eval_support", fromlist=["CASES_PATH"])
            .CASES_PATH))
        p.add_argument("--config", default="config/settings.yaml")
        p.add_argument("--profile", choices=sorted(PROFILE_TOLERANCES),
                       default="ci")

    rec = sub.add_parser("record", help="Record a baseline snapshot")
    add_common(rec)
    rec.add_argument("--mode", choices=["dense", "sparse", "hybrid"],
                     default="hybrid")
    rec.add_argument("--top-k", type=int, default=10)
    rec.add_argument("--rerank", action="store_true")
    rec.add_argument("--baseline", required=True)
    rec.add_argument("--rank-slack", type=int, default=None,
                     help="Override profile rank slack (positions).")
    rec.add_argument("--metric-tolerance", type=float, default=None,
                     help="Override profile aggregate-metric tolerance.")
    rec.add_argument("--fp-allowance", type=int, default=None,
                     help="Extra no-answer false positives allowed.")
    rec.add_argument("--allow-set-drift", action="store_true",
                     help="Do not require the significant-prefix ordering.")
    rec.add_argument("--tolerance-rationale", default=None,
                     help="Recorded justification when overriding tolerances.")
    rec.set_defaults(func=_cmd_record)

    chk = sub.add_parser("check", help="Check current run against a baseline")
    add_common(chk)
    chk.add_argument("--baseline", required=True)
    chk.add_argument("--rank-slack", type=int, default=None)
    chk.add_argument("--metric-tolerance", type=float, default=None)
    chk.set_defaults(func=_cmd_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    return int(ns.func(ns))


if __name__ == "__main__":
    raise SystemExit(main())
