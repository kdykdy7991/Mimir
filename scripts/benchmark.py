#!/usr/bin/env python3
"""
Minimal performance baseline for the Web API query endpoint (M4 收口).

Measures P50 / P95 / max round-trip latency of the synchronous
``POST /collections/{id}/queries`` endpoint over N sample queries, and
compares against the v0.2 budget (**P95 < 2000ms at top_k=10** — see
``docs/plan-2026-07-31-m2-batch3.md`` §4.2).

Prerequisites
-------------
- The Web API is running (``make api`` / ``python -m src.web_api.main``).
- The embedding provider is reachable (``/system/health`` shows embedding
  ``ok``) — the benchmark bails early otherwise.
- The target collection has indexed documents; pass ``--ingest-sample``
  to upload a generated sample PDF first.

Usage
-----
    python -m scripts.benchmark --base-url http://127.0.0.1:8766
    python -m scripts.benchmark --queries 20 --top-k 10
    python -m scripts.benchmark --ingest-sample

Exit code: ``0`` = within budget, ``1`` = over budget or unusable stack.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from typing import Sequence

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8766"
DEFAULT_BUDGET_MS = 2000.0  # v0.2 target: P95 < 2s at top_k=10

# A few representative queries over the sample corpus.
SAMPLE_QUERIES = [
    "什么是向量检索?",
    "混合检索怎么融合?",
    "RAG 有哪些组件?",
    "如何评估检索质量?",
    "BM25 的原理是什么?",
    "PDF 摄取流程包含哪些阶段?",
]


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description="Web API query-latency performance baseline.",
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help=f"Web API base URL (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument("--collection", default="default")
    parser.add_argument(
        "--queries", type=int, default=10,
        help="Number of queries to run (default: 10).",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--budget-ms", type=float, default=DEFAULT_BUDGET_MS,
        help="P95 budget in ms (default: 2000 = the v0.2 target).",
    )
    parser.add_argument(
        "--ingest-sample", action="store_true",
        help="Generate + upload a sample PDF into the collection first.",
    )
    return parser.parse_args(argv)


def _health_ok(client: httpx.Client) -> tuple[bool, str]:
    """Check /system/health — returns (ok, message)."""
    try:
        resp = client.get("/api/v1/system/health", timeout=10)
    except httpx.HTTPError as exc:
        return False, f"cannot reach API at {client.base_url}: {exc}"
    body = resp.json()
    by_name = {d["name"]: d for d in body.get("dependencies", [])}
    emb = by_name.get("embedding")
    if body.get("status") == "ok":
        return True, "all dependencies ok"
    if emb is not None and emb["status"] == "ok":
        return True, "health degraded but embedding ok"
    detail = emb["detail"] if emb else "embedding missing from health"
    return False, f"embedding not usable ({body.get('status')}): {detail}"


def _collection_id(client: httpx.Client, name: str) -> str:
    items = client.get("/api/v1/collections", timeout=10).json()["items"]
    existing = next((c for c in items if c["name"] == name), None)
    if existing is not None:
        return existing["id"]
    resp = client.post(
        "/api/v1/collections", json={"name": name}, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _ingest_sample(client: httpx.Client, collection_id: str) -> None:
    """Generate a sample PDF (``/tmp/smoke.pdf``) and upload it; poll."""
    from tests.smoke.gen_smoke_pdf import main as gen_sample

    gen_sample()  # writes /tmp/smoke.pdf
    with open("/tmp/smoke.pdf", "rb") as fh:
        resp = client.post(
            f"/api/v1/collections/{collection_id}/documents",
            files={"file": ("sample.pdf", fh, "application/pdf")},
            timeout=60,
        )
    resp.raise_for_status()
    task_id = resp.json()["task_id"]

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        task = client.get(f"/api/v1/tasks/{task_id}", timeout=10).json()
        if task["status"] in ("succeeded", "failed"):
            if task["status"] != "succeeded":
                sys.exit(f"sample ingestion failed: {task.get('error')}")
            print(f"[benchmark] ingested sample PDF (task {task_id})")
            return
        time.sleep(0.5)
    sys.exit("sample ingestion did not finish within 120s")


def _run_queries(
    client: httpx.Client, collection_id: str, args: argparse.Namespace,
) -> list[float]:
    """Run the queries; returns per-query round-trip latency in ms."""
    latencies: list[float] = []
    query_pool = SAMPLE_QUERIES * max(1, (args.queries // len(SAMPLE_QUERIES)) + 1)
    for q in query_pool[: args.queries]:
        start = time.perf_counter()
        resp = client.post(
            f"/api/v1/collections/{collection_id}/queries",
            json={"query": q, "top_k": args.top_k, "mode": "hybrid"},
            timeout=60,
        )
        elapsed = (time.perf_counter() - start) * 1000.0
        if resp.status_code != 200:
            sys.exit(
                f"query failed ({resp.status_code}): {resp.text[:300]}",
            )
        latencies.append(elapsed)
    return latencies


def _report(
    latencies: list[float], budget_ms: float, top_k: int,
) -> int:
    sorted_ms = sorted(latencies)
    p50 = statistics.median(sorted_ms)
    p95 = sorted_ms[min(
        len(sorted_ms) - 1, int(round(0.95 * len(sorted_ms))) - 1,
    )]
    p99 = sorted_ms[min(
        len(sorted_ms) - 1, int(round(0.99 * len(sorted_ms))) - 1,
    )]
    mx = sorted_ms[-1]
    print(f"[benchmark] n={len(sorted_ms)} top_k={top_k}")
    print(f"[benchmark] p50={p50:8.1f}ms  p95={p95:8.1f}ms  "
          f"p99={p99:8.1f}ms  max={mx:8.1f}ms")
    ok = p95 <= budget_ms
    print(
        f"[benchmark] budget: p95 < {budget_ms:.0f}ms -> "
        f"{'PASS' if ok else 'FAIL'}",
    )
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        ok, msg = _health_ok(client)
        if not ok:
            print(f"[benchmark] ABORT: {msg}")
            return 1
        print(f"[benchmark] {msg}")

        cid = _collection_id(client, args.collection)
        if args.ingest_sample:
            _ingest_sample(client, cid)

        latencies = _run_queries(client, cid, args)
    return _report(latencies, args.budget_ms, args.top_k)


if __name__ == "__main__":
    raise SystemExit(main())
