"""Measure the post-migration document parsing snapshot with the DocReader backend.

Companion to ``scripts/run_parsing_baseline.py``: that script measures the
*pre-migration* chain (``src.libs.loader.LoaderRegistry`` + ``DocumentChunker``)
and wrote ``docs/baselines/document-parsing/metrics/baseline-*.json``. This
script measures the *post-migration* chain by calling the production DocReader
parser facade (``services/docreader/docreader/parser/parser.Parser``) directly
on the SAME fixed samples and emitting a snapshot that follows the EXACT SAME
JSON schema as the baseline, so the two are comparable.

Chain measured here (mirrors production ``backend=docreader``, builtin engine):
    parse = DocReader ``Parser().parse_file(name, file_type, bytes)``
    chunk = ``SplitterFactory.create(settings.splitter)`` plain recursive split
            (the pre-Phase-4 plain path the committed baseline was measured
            against; keeps ``n_chunks``/``avg_chunk_chars`` comparable).

Offline scope:
    * Vision (VLM OCR, plan §Phase-5 Qwen3.8-27B) cannot run here: there is no
      reachable local VLM endpoint during measurement. Scanned-page OCR metrics
      are therefore reported as ``n/a`` and the scanned sample only verifies
      that the builtin parser routes the page to an image (``scanned_pdf``).
    * OpenDataLoader (plan §Phase-3 table-aware extraction) is not installed
      (``opendataloader_pdf`` missing), so docker-table row/col/header metrics
      are ``n/a``; only the builtin raw-text PDF parser is exercised.

Usage::

    python scripts/run_docreader_parsing_metrics.py \
        [--samples docs/baselines/document-parsing/samples] \
        [--metrics docs/baselines/document-parsing/metrics]

Outputs ``after-<date>.json`` in the metrics dir (schema-identical to baseline).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

# docreader is a sibling package under services/docreader; project root too.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "services" / "docreader"))
sys.path.insert(0, str(_ROOT))

from docreader.parser.parser import Parser  # noqa: E402

DEFAULT_SAMPLES = Path("docs/baselines/document-parsing/samples")
DEFAULT_METRICS = Path("docs/baselines/document-parsing/metrics")

# Same expected cell strings as the legacy baseline (probe parity).
TABLE_EXPECTATIONS = {
    "bordered_table": ["A-1001", "A-1005", "Notebook", "Labels", "125.00", "18.00"],
    "borderless_table": ["A-1001", "A-1003", "Folder", "12.50", "41.00"],
    "cross_page_table": ["B-2001", "B-2006", "C-3001", "Monitor", "360.00", "90.00"],
}

TWO_COLUMN_MARKERS = {"left": ["LEFT-C"], "right": ["RIGHT-C"]}

# DocReader emits Markdown image refs (``![f](images/f)``), unlike the legacy
# ``[IMAGE: ...]`` placeholders. Both represent "no real text, image emitted".
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def probe_two_column_columns(text: str) -> dict:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    left_count = sum(1 for ln in lines if any(m in ln for m in TWO_COLUMN_MARKERS["left"]))
    right_count = sum(1 for ln in lines if any(m in ln for m in TWO_COLUMN_MARKERS["right"]))
    return {"left_marker_lines": left_count, "right_marker_lines": right_count}


def run_one(path: Path, parser: Parser, splitter) -> dict:
    name = path.name
    ftype = "md" if name.endswith(".md") else name.rsplit(".", 1)[-1].lower()
    result: dict = {
        "sample": name,
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
        content = path.read_bytes()
        t0 = time.perf_counter()
        doc = parser.parse_file(name, ftype, content)
        result["load_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    except Exception as exc:  # noqa: BLE001
        result["failure"] = f"parse: {type(exc).__name__}: {exc}"
        return result

    text = doc.content
    result["text_chars"] = len(text)
    result["line_count"] = len(text.splitlines())

    t0 = time.perf_counter()
    parts = splitter.split_text(text)
    result["split_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    result["n_chunks"] = len(parts)
    result["avg_chunk_chars"] = (
        round(sum(len(p) for p in parts) / len(parts), 1) if parts else 0
    )

    stem = path.stem
    probes: dict = {}
    if stem in TABLE_EXPECTATIONS:  # raw ASCII cell-string survival into text
        found = [s for s in TABLE_EXPECTATIONS[stem] if s in text]
        probes["table_digits_found"] = found
        probes["table_digits_total"] = len(TABLE_EXPECTATIONS[stem])
        probes["table_digits_hit_ratio"] = round(len(found) / len(TABLE_EXPECTATIONS[stem]), 3)
        probes["note"] = "raw-text survival only; row/col structure requires OpenDataLoader (offline)"

    if stem == "two_column":
        probes["two_column"] = probe_two_column_columns(text)

    if stem == "scanned":
        real_text = _MD_IMG_RE.sub("", text).strip()
        probes["has_real_text"] = bool(real_text)
        probes["image_placeholder_count"] = len(_MD_IMG_RE.findall(text))
        probes["vision_ocr"] = {
            "status": "n/a",
            "reason": "VLM OCR (plan §Phase-5 Qwen3.8-27B) not reachable offline; "
            "only scanned->image routing verified",
        }

    result["probes"] = probes
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    ap.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    args = ap.parse_args()

    samples_dir: Path = args.samples.expanduser()
    if not samples_dir.is_dir():
        print(f"[docreader-metrics] samples dir not found: {samples_dir}", file=sys.stderr)
        return 2

    from src.core.settings import load_settings
    from src.libs.splitter import SplitterFactory

    settings_path = Path("config/settings.yaml")
    settings = load_settings(str(settings_path)) if settings_path.is_file() else None
    if settings is None:
        from src.core.settings import Settings
        settings = Settings()
    splitter = SplitterFactory.create(settings.splitter)

    parser = Parser()
    records = [
        run_one(p, parser, splitter)
        for p in sorted(samples_dir.iterdir())
        if p.is_file()
    ]

    # Offline-scope annotation. Additive only (does not change the baseline
    # per-sample schema), so the snapshot remains diffable against baseline.
    if any(r.get("probes", {}).get("vision_ocr", {}).get("status") == "n/a"
           or r.get("probes", {}).get("note") for r in records):
        offline_scope = {
            "vision_ocr": {
                "status": "n/a",
                "reason": "no local VLM (Qwen3.8-27B) endpoint reachable during measurement",
                "affected_probes": ["scanned/vision_ocr", "table row/col & header metrics"],
            },
            "opendataloader": {
                "status": "n/a",
                "reason": "opendataloader_pdf module not installed (offline); "
                "only builtin PDF parser exercised",
                "affected_probes": ["table row/col / header retention (plan §Phase-3)"],
            },
        }
    else:
        offline_scope = None

    metrics_dir = args.metrics.expanduser()
    metrics_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    snapshot = {
        "generated_by": "scripts/run_docreader_parsing_metrics.py",
        "date": today,
        "splitter_type": settings.splitter.type,
        "docs": "docs/plan-2026-09-09-weknora-local-document-parser-migration.md",
        "backend": "docreader",
        "engine": "builtin",
        "offline_scope": offline_scope,
        "samples": records,
    }
    out_path = metrics_dir / f"after-{today}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2)

    print("=== Parsing metrics (post-migration, docreader backend/builtin) ===")
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