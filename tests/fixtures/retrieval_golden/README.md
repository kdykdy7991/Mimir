# Retrieval Golden Set v1

Versioned, fully synthetic retrieval regression corpus for WeKnora MCP
roadmap Task 01. It freezes the **retrieval-layer** behavior of the
pipeline (query processing → dense / sparse / RRF hybrid / optional
rerank) *before* any retrieval changes land.

## Scope (and explicit non-scope)

- **In scope**: ranking correctness of the application query interface
  over parsed/extracted **text** — exact keyword matching, CJK
  1+2-gram lexical semantics, multi-column/table/OCR *linearized text*
  content types, page/source metadata, metadata filters, no-answer
  behavior.
- **Not in scope**: PDF layout reconstruction, OCR model quality,
  neural embedding semantics, answer generation. The multi-column,
  table and image categories model what those extractors *emit into
  chunks* (`content_type=multicolumn_text|table_text|ocr_text`,
  `doc_type=pdf|image`, `page`); they do not evaluate the extractors.
  Real-model embedding/rerank profiles are separate explicit run
  profiles in Task 01.5, not this offline corpus.

## Files

| File | Role |
| --- | --- |
| `corpus.json` | Hand-authored source of truth: 7 synthetic documents, 18 chunks |
| `schema.json` | JSON Schema (2020-12) for one `cases.jsonl` record |
| `cases.jsonl` | 8 golden cases across the 6 required categories |
| `data/` | **Generated, git-ignored**: Chroma + BM25 indexes and `build_manifest.json` |

Everything in `corpus.json` is fictional: product names
(ZephyrFlow, XK-2200, KZ-5500G), companies, policies, numbers and
people. No user documents or private text exist here or ever will.

## Stable identity

Ids reuse the production formulas verbatim — the corpus is not a
parallel id scheme:

- document: `document_uuid("golden_v1", source_path)` —
  `uuid5(NAMESPACE_DNS, "document:{collection}:{source_path}")` via
  `src.application.identifiers.document_uuid`;
- chunk: `{document_id}_{index:04d}_{sha256(text)[:8]}` via
  `DocumentChunker._generate_chunk_id`;
- BM25 index: `data/db/bm25/golden_v1.json`; Chroma collection:
  `golden_v1` under `data/db/chroma/`.

Because ids are content-addressed, changing a chunk's text changes its
id on purpose: bump `CORPUS_REVISION` in `scripts/eval_support.py`,
re-seed, and update `relevant_chunk_ids` (the validation test fails
loudly until you do).

## Rebuild

```bash
.venv/bin/python scripts/seed_retrieval_fixtures.py --rebuild
# → data/db/chroma/, data/db/bm25/golden_v1.json, data/build_manifest.json
```

The build is deterministic: real Chroma + real BM25 production code,
dense vectors from `DeterministicHashEmbedding` (BLAKE2b-hashed
production token stream into signed 512 dimensions, L2-normalized —
lexical-overlap geometry, no network, no `PYTHONHASHSEED` dependence).
The manifest is a pure function of the corpus (no timestamps/hostnames),
so two rebuilds compare byte-equal.

## Cases

| id | category | Query (excerpt) | Target |
| --- | --- | --- | --- |
| kw-en-quixbuckle | exact_keyword | quixbuckle latch procedure / ZephyrFlow rollback | runbook chunk 0 (rollback decoy doc present) |
| zh-semantic-canteen-penalty | chinese_semantic | 档口浪费情节严重时的处理措施 | penalty clause (question vs regulation phrasing) |
| kw-zh-overtime | exact_keyword | 加班申请和调休 | attendance chunk 0 |
| xk2200-ip65-multicolumn | multicolumn_pdf | XK-2200 防护等级 | spec sheet page 1, filter `doc_type=pdf` |
| sales-south-q2-table | table | 华南大区第二季度营收 | linearized table row 1120 (north row is a decoy) |
| kz5500g-serial-ocr | image_ocr | KZ-5500G 铭牌序列号 | OCR nameplate text |
| xk2200-warranty-page | exact_keyword | XK-2200 保修期限 | second chunk of same PDF, page 1 |
| noanswer-florpsnickle | no_answer | florpsnickle brumble treaty ratification | zero expected evidence |

## Known system behavior captured (not fixed here)

- Sparse (BM25) returns `[]` for the no-answer case; the dense leg has
  no similarity threshold and Chroma always returns `top_k` rows (zero
  similarity), so dense/hybrid currently surface rows. Baselines record
  this asymmetry; changing it is a product decision outside Task 01.
- The dense path has no chunk-id tie-break; RRF/BM25 ties are broken by
  chunk id. The evaluator normalizes dense ties at the metrics layer.
