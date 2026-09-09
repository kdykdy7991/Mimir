"""Measure the pre-migration document parsing baseline on fixed samples.

This mirrors the *production* load+chunk chain from ``scripts.ingest``
(LoaderRegistry.from_settings + DocumentChunker(SplitterFactory)) so the
numbers reflect what end-to-end ingestion actually does today, before the
DocReader migration.

Usage::

    python scripts/run_parsing_baseline.py [--samples DIR] [--metrics DIR]

For each sample it records:
  - loader wall-clock (ms) and result shape (text length, char count, pages)
  - chunker wall-clock (ms) and chunk count / avg size
  - a few sample-specific "structure probes" that later phases must preserve
    or fix (table digits found, two-column interleave detected, scanned page
    produced text, malformed handling)

Output: a human-readable summary on stdout and a JSON blob saved under the
metrics dir keyed by date, so the baseline is reproducible and diffable.

Design notes
------------
- ``--metrics`` defaults to ``docs/baselines/document-parsing/metrics`` and
  the JSON filename embeds the run date. The committed ``baseline-*.json`` is
  an intentional baseline artifact (Phase 0 acceptance), not a stray temp file.
- No secrets, no base64, no document body is written — only counts/timings and
  tiny non-sensitive probes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

# Ensure project root importable when run as ``python scripts/...``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.settings import load_settings  # noqa: E402
from src.ingestion.chunking.document_chunker import DocumentChunker  # noqa: E402
from src.libs.loader import LoaderRegistry  # noqa: E402
from src.libs.splitter import SplitterFactory  # noqa: E402

DEFAULT_SAMPLES = Path("docs/baselines/document-parsing/samples")
DEFAULT_METRICS = Path("docs/baselines/document-parsing/metrics")

# Expected cell values per table sample: (sample stem, expected_digit_strings)
TABLE_EXPECTATIONS = {
    "bordered_table": ["A-1001", "A-1005", "Notebook", "Labels", "125.00", "18.00"],
    "borderless_table": ["A-1001", "A-1003", "Folder", "12.50", "41.00"],
    "cross_page_table": ["B-2001", "B-2006", "C-3001", "Monitor", "360.00", "90.00"],
}

# Marker phrases used by the two-column fixture, grouped by reading column.
TWO_COLUMN_MARKERS = {
    "left": ["LEFT-C"],
    "right": ["RIGHT-C"],
}


def probe_two_column_columns(text: str) -> dict:
    """Heuristic: count lines carrying each column marker.

    In a correctly ordered two-column PDF the left/right markers repeat
    interleaved by row; this probe just records the per-column line counts for
    the baseline. Two columns with ~4 left + ~2 right lines is expected.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    left_count = sum(1 for ln in lines if any(m in ln for m in TWO_COLUMN_MARKERS["left"]))
    right_count = sum(1 for ln in lines if any(m in ln for m in TWO_COLUMN_MARKERS["right"]))
    return {
        "left_marker_lines": left_count,
        "right_marker_lines": right_count,
    }


def run_one(path: Path, registry: LoaderRegistry, chunker: DocumentChunker) -> dict:
    result: dict = {
        "sample": path.name,
        "bytes": path.stat().st_size,
        "load_ms": None,
        "split_ms": None,
        "text_chars": None,
        "line_count": None,
        "n_chunks": None,
        "avg_chunk_chars": None,
        "probes": {},
        "failure": None,
    }
    try:
        t0 = time.perf_counter()
        doc = registry.load(str(path))
        result["load_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
        result["text_chars"] = len(doc.text)
        result["line_count"] = len(doc.text.splitlines())
    except Exception as exc:  # noqa: BLE001
        result["failure"] = f"load: {type(exc).__name__}: {exc}"
        return result

    t0 = time.perf_counter()
    chunks = chunker.split_document(doc)
    result["split_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    result["n_chunks"] = len(chunks)
    result["avg_chunk_chars"] = (
        round(sum(len(c.text) for c in chunks) / len(chunks), 1)
        if chunks else 0
    )

    stem = path.stem
    text = doc.text
    probes: dict = {}

    # Table digit probe: how many expected cell strings survive to document text.
    if stem in TABLE_EXPECTATIONS:
        found = [s for s in TABLE_EXPECTATIONS[stem] if s in text]
        probes["table_digits_found"] = found
        probes["table_digits_total"] = len(TABLE_EXPECTATIONS[stem])
        probes["table_digits_hit_ratio"] = round(len(found) / len(TABLE_EXPECTATIONS[stem]), 3)

    if stem == "two_column":
        probes["two_column"] = probe_two_column_columns(text)

    if stem == "scanned":
        # A scanned page has no real text: the loader emits only a
        # ``[IMAGE: ...]`` placeholder. Strip it before deciding.
        import re
        real_text = re.sub(r"\[IMAGE:\s*[^\]]+\]", "", text).strip()
        probes["has_real_text"] = bool(real_text)
        probes["image_placeholder_count"] = len(re.findall(r"\[IMAGE:\s*[^\]]+\]", text))

    result["probes"] = probes
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    ap.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    args = ap.parse_args()

    samples_dir: Path = args.samples.expanduser()
    if not samples_dir.is_dir():
        print(f"[baseline] samples dir not found: {samples_dir}", file=sys.stderr)
        return 2

    settings = load_settings("config/settings.yaml") if Path("config/settings.yaml").is_file() else None
    if settings is None:
        from src.core.settings import Settings
        settings = Settings()
    splitter = SplitterFactory.create(settings.splitter)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        registry = LoaderRegistry.from_settings(
            image_dir=str(Path(tmp) / "images"),
            image_classifier=settings.ingestion.image_classifier,
        )
        chunker = DocumentChunker(splitter)

        records: list[dict] = []
        for path in sorted(samples_dir.iterdir()):
            if not path.is_file():
                continue
            records.append(run_one(path, registry, chunker))

    metrics_dir = args.metrics.expanduser()
    metrics_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "generated_by": "scripts/run_parsing_baseline.py",
        "date": date.today().isoformat(),
        "splitter_type": settings.splitter.type,
        "docs": f"docs/plan-2026-09-09-weknora-local-document-parser-migration.md",
        "samples": records,
    }
    out_path = metrics_dir / f"baseline-{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    # Human-readable summary.
    print("=== Parsing baseline (pre-migration, legacy loader) ===")
    for rec in records:
        print(
            f"  {rec['sample']:<22} chars={rec['text_chars']!s:>7} "
            f"lines={rec['line_count']!s:>4} chunks={rec['n_chunks']!s:>3} "
            f"load={rec['load_ms']!s:>8}ms split={rec['split_ms']!s:>7}ms"
        )
        if rec["failure"]:
            print(f"      FAILURE: {rec['failure']}")
        for k, v in rec["probes"].items():
            print(f"      probe {k} = {v}")
    print(f"wrote snapshot -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())