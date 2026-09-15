#!/usr/bin/env python3
"""
Offline retrieval evaluation runner for Golden Set v1 (Task 01.4).

Runs the Golden Set cases through the SAME application-layer retrieval
composition root used by the MCP in-process client
(``scripts.query.build_query_components`` + ``QueryService``) — no Web
and no MCP network layer, and no second retrieval implementation. The
only offline embedding is ``DeterministicHashEmbedding``.

Outputs machine-readable JSON (and optional Markdown) with per-case and
aggregate metrics:

* Recall@K / Precision@K / MRR / nDCG@K (K = 1, 5, top_k), document and
  chunk hit rate;
* no-answer accuracy and false positives, reported separately from
  infrastructure errors;
* P50/P95/mean latency (``time.perf_counter`` monotonic clock) and
  average returned-character count;
* per-branch contribution (dense/sparse candidate counts and the
  source marker of each returned row);
* per-case expected vs actual ids, ranks, scores, retrieval branch and
  a SAFE 120-char preview (full chunk text is never written).

Tie normalization: rows are re-ordered by ``(score desc, chunk_id asc)``
before rank metrics so the dense path's missing tie-break cannot make
metrics run-dependent; production retrieval itself is untouched.

Exit codes
----------
0  evaluation completed (degraded rerank still exits 0, marked in JSON)
1  infrastructure failure (missing seeded data, retrieval raised; cases
   are marked status=error, never counted as misses/no-answer)
2  bad arguments / invalid cases file
3  run skipped as a whole (e.g. requested embedding provider unavailable)

Usage
-----
    .venv/bin/python scripts/seed_retrieval_fixtures.py
    .venv/bin/python scripts/run_retrieval_eval.py \
        --mode hybrid --top-k 10 \
        --output tests/fixtures/retrieval_golden/results/hybrid.json \
        --report-md tests/fixtures/retrieval_golden/results/hybrid.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jsonschema import Draft202012Validator

from scripts.eval_metrics import (
    aggregate,
    mean,
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    tie_normalized_rows,
)
from scripts.eval_support import (
    CASES_PATH,
    CASE_SCHEMA_PATH,
    DEFAULT_DATA_DIR,
    EMBED_PROFILE,
    GOLDEN_COLLECTION,
    MANIFEST_NAME,
    DeterministicHashEmbedding,
)
from scripts.query import build_query_components
from src.application.identifiers import document_uuid
from src.application.services import QueryService
from src.core.settings import Settings, VectorStoreSettings, load_settings
from src.libs.vector_store.chroma_store import ChromaStore

SCHEMA_VERSION = "retrieval-eval-v1"
PREVIEW_CHARS = 120
EXIT_OK = 0
EXIT_INFRA = 1
EXIT_USAGE = 2
EXIT_SKIPPED = 3


# ---------------------------------------------------------------------------
# Input loading / validation
# ---------------------------------------------------------------------------

def load_cases(path: Path, validator: Draft202012Validator) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"cases file not found: {path}")
    cases: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        errors = sorted(e.message for e in validator.iter_errors(case))
        if errors:
            raise ValueError(f"{path}:{line_no} ({case.get('id')}): {errors}")
        collections = set(case["collection_ids"])
        if collections != {GOLDEN_COLLECTION}:
            raise ValueError(
                f"{path}:{line_no}: eval v1 supports only {GOLDEN_COLLECTION}, "
                f"got {sorted(collections)}",
            )
        cases.append(case)
    if not cases:
        raise ValueError(f"no cases found in {path}")
    return cases


def _safe_preview(text: str) -> str:
    """Bounded preview with no newlines; the full text is never emitted."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= PREVIEW_CHARS else flat[: PREVIEW_CHARS - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Pipeline construction
# ---------------------------------------------------------------------------

def build_service(data_dir: Path, mode_note: dict[str, Any]) -> QueryService:
    store = ChromaStore(
        VectorStoreSettings(
            backend="chroma",
            persist_path=str(data_dir / "db" / "chroma"),
            collection_name=GOLDEN_COLLECTION,
        ),
    )
    hybrid = build_query_components(
        data_dir=str(data_dir),
        collection=GOLDEN_COLLECTION,
        embedding=DeterministicHashEmbedding(),
        vector_store=store,
    )
    mode_note["embedding_profile"] = EMBED_PROFILE
    return QueryService(hybrid)


def build_rerank_stage(requested: bool, config_path: Path) -> tuple[Any, dict]:
    """Mirror src/mcp_server/clients/in_process.py construction semantics:
    backend 'none'/factory failure → explicit skip, never zero recall."""
    info = {"requested": requested, "applied": False, "status": "off", "reason": None}
    if not requested:
        return None, info
    try:
        settings = (
            load_settings(str(config_path))
            if config_path.is_file() else Settings()
        )
        if settings.rerank.backend == "none":
            info["status"] = "skipped"
            info["reason"] = "rerank.backend is 'none'"
            return None, info
        from src.core.query_engine.reranker import RerankerStage
        from src.libs.reranker import RerankerFactory

        stage = RerankerStage(
            reranker=RerankerFactory.create(settings.rerank),
            top_m=settings.rerank.top_m,
        )
        info["applied"] = True
        info["status"] = "applied"
        info["backend"] = settings.rerank.backend
        return stage, info
    except Exception as exc:  # noqa: BLE001 — availability is reported, not raised
        info["status"] = "skipped"
        info["reason"] = f"{type(exc).__name__}: {exc}"
        return None, info


# ---------------------------------------------------------------------------
# Per-case evaluation
# ---------------------------------------------------------------------------

def evaluate_case(
    case: dict,
    *,
    service: QueryService,
    mode: str,
    top_k: int,
    ks: list[int],
    rerank_stage: Any,
) -> dict:
    expected_chunks = list(case["relevant_chunk_ids"])
    expected_docs = set(case["relevant_document_ids"])
    start = time.perf_counter()
    try:
        result = service.search(
            case["query"],
            mode=mode,
            top_k=top_k,
            filters=case.get("filters") or None,
        )
        chunks = result.chunks
        fallback = False
        if rerank_stage is not None and chunks:
            output = rerank_stage.rerank(case["query"], chunks)
            chunks = output.results
            fallback = output.fallback
        latency_ms = (time.perf_counter() - start) * 1000.0
    except Exception as exc:  # noqa: BLE001 — infra failure, never a "miss"
        return {
            "id": case["id"],
            "category": case["category"],
            "status": "error",
            "expect_no_answer": bool(case.get("expect_no_answer", False)),
            "latency_ms": (time.perf_counter() - start) * 1000.0,
            "error_kind": type(exc).__name__,
            "error": str(exc)[:300],
        }

    raw_rows = [
        {
            "chunk_id": r.chunk_id,
            "score": float(r.score) if r.score is not None else None,
            "source": r.source or "fusion",
            "document_id": str(
                document_uuid(GOLDEN_COLLECTION, r.metadata.get("source_path") or ""),
            ),
            "text": r.text or "",
        }
        for r in chunks
    ]
    rows = tie_normalized_rows(raw_rows)
    retrieved_ids = [row["chunk_id"] for row in rows]
    returned_docs = {row["document_id"] for row in rows}

    metrics = {
        f"recall_at_{k}": recall_at_k(retrieved_ids, expected_chunks, k)
        for k in ks
    }
    metrics.update({
        f"precision_at_{k}": precision_at_k(retrieved_ids, expected_chunks, k)
        for k in ks
    })
    metrics.update({
        f"ndcg_at_{k}": ndcg_at_k(retrieved_ids, expected_chunks, k) for k in ks
    })
    metrics.update({
        "reciprocal_rank": reciprocal_rank(retrieved_ids, expected_chunks),
        "relevant_ranks": [
            rank for rank in (
                i for i, cid in enumerate(retrieved_ids, 1) if cid in set(expected_chunks)
            )
        ],
        "relevant_chunks_retrieved": len(set(retrieved_ids) & set(expected_chunks)),
        "document_hit": bool(returned_docs & expected_docs),
        "returned_count": len(rows),
        "returned_chars": sum(len(row["text"]) for row in rows),
    })

    actual = [
        {
            "rank": i,
            "chunk_id": row["chunk_id"],
            "document_id": row["document_id"],
            "score": row["score"],
            "branch": row["source"],
            "safe_preview": _safe_preview(row["text"]),
        }
        for i, row in enumerate(rows, 1)
    ]
    return {
        "id": case["id"],
        "category": case["category"],
        "status": "ok",
        "expect_no_answer": bool(case.get("expect_no_answer", False)),
        "expected_chunk_ids": expected_chunks,
        "expected_document_ids": sorted(expected_docs),
        "actual": actual,
        "metrics": metrics,
        "latency_ms": latency_ms,
        "degraded": bool(result.degraded),
        "degraded_reasons": list(result.degraded_reasons),
        "rerank_fallback": fallback,
        "branch_counts": {
            "dense_candidates": result.dense_count,
            "sparse_candidates": result.sparse_count,
            "fused_candidates": result.fused_count,
        },
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# Retrieval Golden Set Evaluation",
        "",
        f"- mode: `{report['mode']}` · top_k: {report['top_k']} · "
        f"profile: {report['profile']} · status: **{report['status']}**",
        f"- embedding: `{report['embedding_profile']}` · corpus: "
        f"`{report.get('corpus_revision')}` · rerank: {report['rerank']['status']}",
        f"- cases: {report['case_count']} "
        f"(answerable evaluated: {s['answerable_evaluated']}, "
        f"errors: {report['error_count']}, no-answer: "
        f"{s['no_answer_correct']}/{s['no_answer_total']} correct)",
        "",
        "## Aggregate metrics",
        "",
        "| metric | value |",
        "| --- | ---: |",
    ]
    for key, value in s["recall"].items():
        lines.append(f"| {key} | {_fmt(value)} |")
    for key, value in s["precision"].items():
        lines.append(f"| {key} | {_fmt(value)} |")
    for key, value in s["ndcg"].items():
        lines.append(f"| {key} | {_fmt(value)} |")
    lines += [
        f"| MRR | {_fmt(s['mrr'])} |",
        f"| document_hit_rate | {_fmt(s['document_hit_rate'])} |",
        f"| chunk_hit_rate | {_fmt(s['chunk_hit_rate'])} |",
        f"| no_answer_accuracy | {_fmt(s['no_answer_accuracy'])} |",
        f"| no_answer_false_positives | {s['no_answer_false_positives']} |",
        f"| latency P50/P95/mean ms | {s['latency_ms']['p50']:.1f} / "
        f"{s['latency_ms']['p95']:.1f} / {s['latency_ms']['mean']:.1f} |",
        f"| avg returned chars/chunk | {_fmt(s['avg_returned_chars'])} |",
        "",
        "## Per-case",
        "",
        "| case | category | status | rel rank(s) | nDCG@min(5,K) | latency ms |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    ndcg_key = f"ndcg_at_{min(5, report['top_k'])}"
    for case in report["cases"]:
        if case["status"] != "ok":
            lines.append(
                f"| {case['id']} | {case['category']} | error "
                f"({case.get('error_kind', '?')}) | — | — | "
                f"{case['latency_ms']:.1f} |",
            )
            continue
        ranks = ",".join(map(str, case["metrics"]["relevant_ranks"])) or "-"
        lines.append(
            f"| {case['id']} | {case['category']} | ok | {ranks} | "
            f"{_fmt(case['metrics'].get(ndcg_key))} | {case['latency_ms']:.1f} |",
        )
    if report["errors"]:
        lines += ["", "## Infrastructure errors", ""]
        for err in report["errors"]:
            lines.append(
                f"- **{err['id']}** ({err['error_kind']}): {err['error']}",
            )
    if report["degraded_reasons"]:
        lines += ["", "## Degraded signals", ""]
        for reason in sorted(set(report["degraded_reasons"])):
            lines.append(f"- {reason}")
    lines.append("")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_evaluation(args: argparse.Namespace) -> tuple[dict, int]:
    data_dir = Path(args.data_dir)
    cases_path = Path(args.cases)
    schema = json.loads(CASE_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        cases = load_cases(cases_path, Draft202012Validator(schema))
    except ValueError as exc:
        return {"status": "usage_error", "error": str(exc)}, EXIT_USAGE

    manifest_path = data_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return {
            "status": "infra_error",
            "error": (
                f"golden data dir is not seeded: {manifest_path} missing; "
                "run scripts/seed_retrieval_fixtures.py first"
            ),
        }, EXIT_INFRA
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("embedding_profile") != EMBED_PROFILE:
        return {
            "status": "infra_error",
            "error": (
                f"data dir embedding profile {manifest.get('embedding_profile')!r} "
                f"does not match evaluator profile {EMBED_PROFILE!r}; re-seed"
            ),
        }, EXIT_INFRA
    if args.mode in ("sparse", "hybrid"):
        bm25_path = data_dir / "db" / "bm25" / f"{GOLDEN_COLLECTION}.json"
        if not bm25_path.is_file():
            return {
                "status": "infra_error",
                "error": f"BM25 index missing for mode={args.mode}: {bm25_path}",
            }, EXIT_INFRA

    try:
        service = build_service(data_dir, {})
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "skipped",
            "error": f"{type(exc).__name__}: {exc}",
        }, EXIT_SKIPPED

    rerank_stage, rerank_info = build_rerank_stage(
        args.rerank, Path(args.config),
    )

    ks = sorted({1, min(5, args.top_k), args.top_k})
    case_reports = [
        evaluate_case(
            case, service=service, mode=args.mode, top_k=args.top_k,
            ks=ks, rerank_stage=rerank_stage,
        )
        for case in cases
    ]

    summary = aggregate(case_reports, ks).as_dict()
    ok_cases = [c for c in case_reports if c["status"] == "ok"]
    latencies = [c["latency_ms"] for c in ok_cases]
    total_chars = sum(c["metrics"].get("returned_chars", 0) for c in ok_cases)
    total_rows = sum(c["metrics"].get("returned_count", 0) for c in ok_cases)
    summary["latency_ms"] = {
        "p50": round(percentile(latencies, 50), 3),
        "p95": round(percentile(latencies, 95), 3),
        "mean": round(mean(latencies), 3),
    }
    summary["avg_returned_chars"] = (
        round(total_chars / total_rows, 2) if total_rows else 0.0
    )
    summary["branch_contribution"] = {
        "dense_candidates_total": sum(
            (c.get("branch_counts") or {}).get("dense_candidates") or 0
            for c in ok_cases
        ),
        "sparse_candidates_total": sum(
            (c.get("branch_counts") or {}).get("sparse_candidates") or 0
            for c in ok_cases
        ),
        "returned_by_source": _source_counts(ok_cases),
    }

    errors = [
        {"id": c["id"], "error_kind": c["error_kind"], "error": c["error"]}
        for c in case_reports if c["status"] == "error"
    ]
    degraded_reasons = [
        reason
        for c in ok_cases for reason in c.get("degraded_reasons", [])
    ]
    if rerank_info["status"] == "skipped":
        degraded_reasons.append(
            f"rerank requested but unavailable: {rerank_info.get('reason')}",
        )

    if errors:
        status = "error"
        exit_code = EXIT_INFRA
    elif rerank_info["status"] == "skipped":
        status = "degraded"
        exit_code = EXIT_OK
    else:
        status = "ok"
        exit_code = EXIT_OK

    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "profile": args.profile,
        "mode": args.mode,
        "top_k": args.top_k,
        "ks": ks,
        "collection": GOLDEN_COLLECTION,
        "corpus_revision": manifest.get("corpus_revision"),
        "corpus_sha256": manifest.get("corpus_sha256"),
        "embedding_profile": EMBED_PROFILE,
        "rerank": rerank_info,
        "tie_break": "score_desc_then_chunk_id",
        "case_count": len(cases),
        "error_count": len(errors),
        "summary": summary,
        "cases": case_reports,
        "errors": errors,
        "degraded_reasons": sorted(set(degraded_reasons)),
    }
    return report, exit_code


def _source_counts(ok_cases: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in ok_cases:
        for row in case.get("actual", []):
            counts[row["branch"]] = counts.get(row["branch"], 0) + 1
    return dict(sorted(counts.items()))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--mode", choices=["dense", "sparse", "hybrid"],
                        default="hybrid")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--rerank", action="store_true",
                        help="Apply the configured reranker (skipped/degraded if unavailable).")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--profile", choices=["ci", "release"], default="ci")
    parser.add_argument("--output", help="Write JSON report here.")
    parser.add_argument("--report-md", help="Write Markdown report here.")
    args = parser.parse_args(argv)
    if not 1 <= args.top_k <= 50:
        parser.error("--top-k must be in [1, 50]")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report, exit_code = run_evaluation(args)

    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        print(f"wrote {out} (status={report['status']})", file=sys.stderr)
    else:
        sys.stdout.write(payload)
    if args.report_md and exit_code != EXIT_USAGE:
        md = Path(args.report_md)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(render_markdown(report), encoding="utf-8")
        print(f"wrote {md}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
