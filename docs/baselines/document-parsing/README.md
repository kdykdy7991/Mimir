# Document parsing baseline corpus

> Project: SKDY-RAG-SERVER — docreader migration
> Plan: `docs/plan-2026-09-09-weknora-local-document-parser-migration.md`
> This corpus is the **fixed** Phase-0 baseline/acceptance input set (Phase 13
> gates are measured against these exact files, not "by eye").

## Layout

```text
docs/baselines/document-parsing/
├── README.md                  this file
├── samples/                   the fixed input files (small, non-sensitive)
├── descriptors/               expected-structure notes (incl. future formats)
└── metrics/                   baseline measurement snapshots (JSON)
```

## Reproducing the corpus

All inputs are regenerated deterministically by:

```bash
python scripts/generate_parsing_baseline_samples.py --out docs/baselines/document-parsing/samples
```

Measure the pre-migration baseline with:

```bash
python scripts/run_parsing_baseline.py \
    --samples docs/baselines/document-parsing/samples \
    --metrics docs/baselines/document-parsing/metrics
```

The runner mirrors the production `scripts/ingest.build_pipeline` load+chunk
chain (`LoaderRegistry.from_settings` + `DocumentChunker(SplitterFactory)`), so
its timings/shapes reflect what end-to-end ingestion does *today*.

## Current samples (parseable by the legacy loader: PDF + Markdown)

| file | purpose | probes recorded |
| --- | --- | --- |
| `single_column.pdf` | single-column digital PDF | text shape |
| `two_column.pdf` | two-column layout; legacy `(y,x)` sort interleaves columns | left/right marker line counts |
| `bordered_table.pdf` | bordered table (5 rows) | expected cell-string hit ratio |
| `borderless_table.pdf` | borderless table (5 rows), row/col inference hard | same |
| `cross_page_table.pdf` | table spanning 2 pages | expected cell-string hit ratio |
| `scanned.pdf` | image-only scanned page (no text layer) | has_real_text / image placeholder count |
| `docs.md` | multi-section Markdown with GFM table | text shape |

Generators note: PDF text is ASCII-only because the production PDF stack
(pymupdf base-14 fonts) cannot render CJK glyphs; CJK fidelity is exercised by
the Markdown sample and by Phase-6 real-type fixtures.

## Baseline evidence captured (2026-09-09)

`metrics/baseline-2026-09-09.json` records:

- **Digit/string survival** is high (all expected ASCII cell strings survive to
  document text). This is *raw text survival only* — the legacy loader flattens
  table cells into coordinate-sorted **text lines**, so it exposes **no**
  row-column/table structure, no header recognition, and no cross-chunk header
  tracking. Column-count / header-retention / table-number accuracy as defined
  in Phase 13 are therefore effectively **not measured** by the legacy loader
  (they are the improvement targets of Phases 2–4).
- **Two-column bug**: the extracted order for `two_column.pdf` interleaves
  columns because lines rarely share an exact y:
  ```
  LEFT header, RIGHT para1, LEFT para1, RIGHT para2, LEFT para2, LEFT para3, FOOTER, FOOTER
  ```
  This is the concrete regression Phase 2 must fix.
- **Scanned page**: `scanned.pdf` yields no real text, exactly one image, and
  the legacy loader emits one `[IMAGE: ...]` placeholder (no OCR/VLM path).
- **Chunking**: each short sample collapses into a single chunk under the
  current `splitter` (recursive, size 1024). Table-aware protection/header
  tracking do not exist yet.

## Future-format descriptors (no binaries yet)

DOCX / XLSX / PPTX / OCR-style image tables are **intentionally not** generated
in Phase 0: the legacy baseline chain supports only PDF + Markdown, and creating
the binaries would require openpyxl/python-docx/python-pptx that are not (yet)
project dependencies. Their *expected structure* is documented under
`descriptors/` and real fixtures are added when each format is migrated in
Phase 6.

## Hygiene

- All files here are small and contain only synthetic, non-sensitive content.
- No base64, no real document text, no model weights, no environment config.
- Run the generator + runner to confirm 0 stray binaries (e.g. updated scan
  masks) before committing.