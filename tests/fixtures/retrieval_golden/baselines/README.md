# Retrieval Baselines — Golden Set v1

Frozen retrieval-quality baselines for
`tests/fixtures/retrieval_golden/cases.jsonl`, recorded by
`scripts/eval_gate.py record` and enforced by `... check`. These are
**fingerprints**, not full reports: contract identity, per-case expected
ids and observed chunk-id rankings, aggregate metrics. They never
contain latency, scores, or chunk text; serialization is sorted-key JSON
and byte-deterministic.

## Files (`deterministic-hash-v1/`, profile `ci`)

| baseline | mode | rerank | rank slack | metric tol | strict prefix | no-answer |
| --- | --- | --- | ---: | ---: | --- | --- |
| `dense.json` | dense | off | 1 | 0.02 | no | FP=1 (known: no score threshold) |
| `sparse.json` | sparse | off | 0 | 0.0 | yes | strict empty (FP=0) |
| `hybrid.json` | hybrid | off | 0 | 0.0 | yes | FP=1 via dense branch |
| `hybrid-rerank.json` | hybrid | **skipped** | 0 | 0.0 | yes | as hybrid |

`hybrid-rerank.json` is the honest environment-block record for this
machine: `rerank.backend: none` (no cross-encoder/LLM provider), so the
runner exits 0 with `status=degraded`, `rerank.status=skipped`, and
hybrid rankings unchanged. On a host with a rerank backend the contract
check (`rerank.status`) intentionally fails: that profile must be
recorded separately under a real-model profile.

## Why dense carries tolerances and sparse/hybrid do not

Embeddings are deterministic, but Chroma's HNSW graph orders near-tie
**zero-signal** neighbors slightly differently between independently
built collections (separate seeded data dirs). Measured over 4
independent builds of the same corpus, dense drift was at most:

- first relevant rank: 3 → 4 on one case (`rank_slack=1`);
- aggregate MRR: −0.012; nDCG@5/@10: −0.010 (`metric_tolerance=0.02`);
- Recall@K and document/chunk hit rates: unchanged.

The gate still enforces the **significant prefix** logic at check time
(only rows below the worst relevant rank can churn without affecting
Recall/MRR/nDCG; aggregate Precision@K stays metric-gated). BM25 and
RRF break ties by chunk id, so sparse and hybrid rankings are
byte-reproducible across builds and keep zero tolerances.

## Reproduce

```bash
.venv/bin/python scripts/seed_retrieval_fixtures.py

# check (read-only; works against any freshly seeded data dir)
for f in dense sparse hybrid hybrid-rerank; do
  .venv/bin/python scripts/eval_gate.py check --profile ci \
    --baseline tests/fixtures/retrieval_golden/baselines/deterministic-hash-v1/$f.json
done

# re-record after a DELIBERATE corpus/index change (review the diff)
.venv/bin/python scripts/eval_gate.py record --mode sparse --profile ci \
  --baseline tests/fixtures/retrieval_golden/baselines/deterministic-hash-v1/sparse.json
.venv/bin/python scripts/eval_gate.py record --mode hybrid --rerank --profile ci \
  --baseline tests/fixtures/retrieval_golden/baselines/deterministic-hash-v1/hybrid-rerank.json
```

CI default (`tests/integration/test_eval_baseline_gate.py`): seed a
temporary data dir and check all four snapshots — no network, no model
provider. Exit codes: `0` pass · `1` regression · `2` usage/profile
mismatch · `3` infrastructure failure (inconclusive; never a
zero-recall result).

## Real-model profiles (release gate)

Neural embedding/rerank baselines are out of scope for the default
deterministic gate. Record them explicitly with `--profile release`
(preset: metric tolerance 0.05, rank slack 2, non-strict prefix, FP
allowance 0) against a named provider and review before publishing a
release; do not replace the `ci` snapshots with them.
