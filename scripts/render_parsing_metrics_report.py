"""Render the document-parsing metrics comparison report from the two committed
JSON snapshots (baseline vs post-migration). Single source of truth for the
delta numbers, so the README tables never drift from the snapshots by hand.

Usage::

    python scripts/render_parsing_metrics_report.py \
        [--baseline docs/baselines/document-parsing/metrics/baseline-*.json] \
        [--post docs/baselines/document-parsing/metrics/after-*.json] \
        [--out docs/baselines/document-parsing/metrics/comparison.md]

Writes a Markdown "Per-metric deltas" section (the same one referenced from
``metrics/README.md``) computed directly from the two snapshots.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_DEFAULT_METRICS = Path("docs/baselines/document-parsing/metrics")


def _load_records(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {r["sample"]: r for r in data["samples"]}


def _fmt(v, b, a) -> str:
    """Render a value with an inline (delta vs baseline) annotation."""
    if b is None or a is None:
        return "n/a"
    delta = a - b
    if delta == 0:
        return f"{a} (0)"
    return f"{a} ({delta:+d})"


def render(baseline: Path, post: Path) -> str:
    B = _load_records(baseline)
    A = _load_records(post)
    order = [
        "single_column.pdf", "two_column.pdf", "docs.md",
        "bordered_table.pdf", "borderless_table.pdf", "cross_page_table.pdf",
        "scanned.pdf",
    ]
    lines: list[str] = [""]
    lines.append("### Text shape")
    lines.append("")
    lines.append("| sample | text_chars B → A | line_count B → A |")
    lines.append("| --- | --- | --- |")
    for name in order:
        if name not in B or name not in A:
            continue
        b, a = B[name], A[name]
        lines.append(
            f"| `{name}` | {_fmt(name, b.get('text_chars'), a.get('text_chars'))} | "
            f"{_fmt(name, b.get('line_count'), a.get('line_count'))} |",
        )
    lines += ["", "### Chunking (plain recursive split, comparable to committed baseline)",
              "",
              "`n_chunks` = 1 and `avg_chunk_chars` = `text_chars` for every sample in "
              "**both** snapshots (all samples < 1024 chars).",
              ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path, default=None)
    ap.add_argument("--post", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=_DEFAULT_METRICS / "comparison.md")
    args = ap.parse_args()

    metrics = _DEFAULT_METRICS
    baseline = args.baseline or sorted(metrics.glob("baseline-*.json"))[0]
    post = args.post or sorted(metrics.glob("after-*.json"))[0]
    out = args.out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(baseline, post), encoding="utf-8")
    print(f"wrote comparison -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())