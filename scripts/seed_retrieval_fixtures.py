#!/usr/bin/env python3
"""
Seed the deterministic retrieval Golden Set indexes (Task 01.3).

Builds a throwaway-but-reproduction-exact retrieval data directory from
the hand-authored synthetic corpus
(``tests/fixtures/retrieval_golden/corpus.json``):

* a real persistent Chroma store at ``<data-dir>/db/chroma`` (collection
  ``golden_v1``) using :class:`DeterministicHashEmbedding`;
* a real BM25 index at ``<data-dir>/db/bm25/golden_v1.json``;
* a ``build_manifest.json`` listing every stable document/chunk id.

The default data dir (``tests/fixtures/retrieval_golden/data``) is
git-ignored: indexes are rebuildable artifacts, never committed. Chunk
and document ids are content-addressed by production functions, so they
stay stable across rebuilds and machines.

Usage
-----
    .venv/bin/python scripts/seed_retrieval_fixtures.py [--rebuild]
    .venv/bin/python scripts/seed_retrieval_fixtures.py --data-dir /tmp/golden

Exit codes: 0 success, 2 invalid input/state, 1 infrastructure failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_support import (  # noqa: E402
    CORPUS_PATH,
    DEFAULT_DATA_DIR,
    seed_fixture_data,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", default=str(DEFAULT_DATA_DIR),
        help="Target data directory (default: the git-ignored fixture dir).",
    )
    parser.add_argument(
        "--corpus", default=str(CORPUS_PATH),
        help="Path to the hand-authored golden corpus.json.",
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Rebuild into a non-empty data directory.",
    )
    args = parser.parse_args(argv)

    if not Path(args.corpus).is_file():
        print(f"error: corpus not found: {args.corpus}", file=sys.stderr)
        return 2

    try:
        manifest = seed_fixture_data(
            args.data_dir, corpus_path=args.corpus, force=args.rebuild,
        )
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"error: failed to seed golden indexes: {exc}", file=sys.stderr)
        return 1

    print(
        f"seeded {manifest['chunk_count']} chunks / "
        f"{manifest['document_count']} documents into {args.data_dir} "
        f"(embedding {manifest['embedding_profile']}@{manifest['embedding_dimensions']})",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
