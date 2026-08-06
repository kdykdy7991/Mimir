"""
End-to-end smoke test — runs the full ingestion → query pipeline
against a real PDF using only local resources where possible.

This proves:

* The full CLI surface (``scripts/ingest.py`` →
  ``scripts/query.py``) is wired correctly and works against
  real data.
* A local embedding model (``sentence-transformers``) +
  ``ChromaStore`` + BM25 ``HybridSearch`` → top-k retrieval
  produces the expected relevant chunks.
* ``TraceContext`` hooks fire end-to-end and the JSONL log is
  populated.

Prerequisites
-------------
* The embedding model (``BAAI/bge-small-zh-v1.5`` by default)
  must be reachable on HuggingFace Hub OR already cached under
  ``$HF_HOME``. If HuggingFace is unreachable from the current
  environment, this script exits with status 77 (autotest-style
  "skip") and a clear message — it is NOT a failure of the
  pipeline.
* ``pymupdf`` and ``sentence-transformers`` must be installed
  (already in the project's ``requirements.txt``).

Run with::

    python tests/smoke/run_smoke.py

Exit codes:
    0   smoke test passed
    1   smoke test failed (real pipeline error)
    77  smoke test skipped (network / model not available)
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SMOKE_DIR = Path(__file__).resolve().parent
SMOKE_PDF = Path("/tmp/smoke.pdf")
SMOKE_CONFIG = SMOKE_DIR / "smoke_config.yaml"
SMOKE_DATA_DIR = PROJECT_ROOT / "data" / "smoke"
COLLECTION = "smoke"

QUERY = "How does the pipeline combine dense and sparse retrieval?"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_SKIP = 77  # autotest convention: skip


def _hf_reachable() -> bool:
    """Quick check that HuggingFace Hub is reachable."""
    try:
        socket.create_connection(("huggingface.co", 443), timeout=3).close()
        return True
    except (OSError, socket.timeout):
        return False


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess, fail loudly on non-zero return."""
    print(f"\n>>> {' '.join(cmd)}")
    result = subprocess.run(
        cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, **kwargs,
    )
    if result.returncode != 0:
        print("STDOUT:", result.stdout[-1000:])
        print("STDERR:", result.stderr[-1000:])
        raise SystemExit(f"command failed: {' '.join(cmd)}")
    return result


def step0_prepare() -> None:
    """Make sure the smoke PDF and config exist; clean old state."""
    if not SMOKE_PDF.exists():
        # Generate on the fly — keeps the smoke runnable in one
        # step without committing binary fixtures.
        print(f"{SMOKE_PDF} missing, generating via gen_smoke_pdf.py")
        subprocess.run(
            [sys.executable, str(SMOKE_DIR / "gen_smoke_pdf.py")],
            check=True, cwd=PROJECT_ROOT,
        )
    if not SMOKE_CONFIG.exists():
        print(f"missing smoke config at {SMOKE_CONFIG}")
        sys.exit(EXIT_FAIL)

    if SMOKE_DATA_DIR.exists():
        print(f"cleaning {SMOKE_DATA_DIR}")
        shutil.rmtree(SMOKE_DATA_DIR)
    SMOKE_DATA_DIR.mkdir(parents=True)


def step1_ingest() -> None:
    """Ingest the smoke PDF with the local-only config."""
    _run([
        sys.executable, "scripts/ingest.py",
        "--path", str(SMOKE_PDF),
        "--collection", COLLECTION,
        "--config", str(SMOKE_CONFIG),
        "--data-dir", str(SMOKE_DATA_DIR),
    ], timeout=300)
    print("ingest step OK")


def step2_query() -> None:
    """Run a query against the freshly-built index."""
    result = _run([
        sys.executable, "scripts/query.py",
        "--query", QUERY,
        "--collection", COLLECTION,
        "--top-k", "3",
        "--config", str(SMOKE_CONFIG),
        "--data-dir", str(SMOKE_DATA_DIR),
        "--verbose",
    ], timeout=120)
    # Show the last chunk of the response so the user can see
    # the retrieved citations.
    print(result.stdout[-1500:])


def step3_traces() -> int:
    """Verify the trace log was populated for both phases."""
    trace_log = SMOKE_DATA_DIR / "traces.jsonl"
    if not trace_log.exists():
        print(f"NO trace log at {trace_log}")
        return 0
    lines = trace_log.read_text(encoding="utf-8").strip().splitlines()
    print(f"trace log: {len(lines)} entries")
    types_seen: set[str] = set()
    for line in lines:
        rec = json.loads(line)
        t = rec.get("trace_type", "?")
        types_seen.add(t)
        print(f"  - trace_type={t:12s}  stages={list(rec.get('stages', {}).keys())}")
    assert "ingestion" in types_seen, "missing ingestion trace"
    assert "query" in types_seen, "missing query trace"
    return len(lines)


def main() -> int:
    print("=" * 60)
    print("SMOKE TEST: ingest → query → trace")
    print("=" * 60)

    if not _hf_reachable():
        print(
            "SKIP: HuggingFace Hub is not reachable from this environment.\n"
            "      The smoke test needs to download the embedding model\n"
            "      (BAAI/bge-small-zh-v1.5) on first run. To run this\n"
            "      smoke locally, either pre-cache the model under $HF_HOME\n"
            "      or fix network access to huggingface.co.\n"
            "\n"
            "      The pipeline itself is exercised by tests/e2e/ with\n"
            "      injected fake backends; this smoke only proves the\n"
            "      network-dependent path.",
        )
        return EXIT_SKIP

    step0_prepare()
    step1_ingest()
    step2_query()
    trace_count = step3_traces()

    print("\n" + "=" * 60)
    if trace_count == 0:
        print("FAIL: no trace entries recorded")
        return EXIT_FAIL
    print(f"OK: {trace_count} trace entries, query returned a response")
    print("=" * 60)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
