#!/usr/bin/env python3
"""
Data ingestion script — offline document processing entry point.

Walks the input path (a file or a directory of files), builds a
full :class:`IngestionPipeline` from the project settings, and runs
each supported file through it.

Examples
--------
    # Ingest a single PDF into the default collection
    python scripts/ingest.py --path ./docs/report.pdf

    # Ingest every supported file in a directory under a named
    # collection
    python scripts/ingest.py --path ./docs --collection reports

    # Force re-ingestion even if the file's hash is already marked
    # as success
    python scripts/ingest.py --path ./docs --force

    # Use a custom data directory (handy for tests / isolated runs)
    python scripts/ingest.py --path ./docs --data-dir ./scratch/data

Notes
-----
The pipeline is built from ``--config`` (default
``./config/settings.yaml``). The script's CLI is a thin wrapper; the
real work lives in :mod:`src.ingestion.pipeline` and its sub-modules.

Testability
-----------
The pipeline-building logic is exposed as :func:`build_pipeline`,
which accepts already-constructed collaborators. This lets the e2e
test pass in fake backends (in-memory vector store, deterministic
embedder) so the CLI behavior can be exercised without API keys.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

# Ensure the project root is importable when running directly as
# ``python scripts/ingest.py`` from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.settings import (  # noqa: E402
    Settings,
    load_settings,
)
from src.ingestion.chunking.document_chunker import DocumentChunker  # noqa: E402
from src.ingestion.embedding import (  # noqa: E402
    BatchProcessor,
    DenseEncoder,
    SparseEncoder,
)
from src.ingestion.pipeline import IngestionPipeline  # noqa: E402
from src.ingestion.storage import (  # noqa: E402
    BM25Indexer,
    ImageStorage,
    VectorUpserter,
)
from src.ingestion.transform import (  # noqa: E402
    ChunkRefiner,
    ImageCaptioner,
    ImageContentClassifier,
    MetadataEnricher,
)
from src.libs.loader import (  # noqa: E402
    LoaderRegistry,
    SQLiteIntegrityChecker,
)


logger = logging.getLogger("ingest")

SUPPORTED_EXTS = (".pdf", ".md", ".markdown")


# ---------------------------------------------------------------------------
# Pipeline assembly
# ---------------------------------------------------------------------------

def build_pipeline(
    *,
    settings: Settings,
    data_dir: str,
    collection: str,
    splitter: Any,
    embedding: Any,
    vector_store: Any,
    llm: Any = None,
    document_parser: Any = None,
) -> IngestionPipeline:
    """
    Wire the C2–C13 modules into a single :class:`IngestionPipeline`.

    All collaborators except ``data_dir`` / ``collection`` /
    ``settings`` are injected so the test harness can swap in fake
    backends. ``data_dir`` is the root under which
    ``data_dir/db/`` and ``data_dir/images/`` live.

    ``document_parser`` (optional): a unified :class:`DocumentParser`
    selected by the ``document_parser.backend`` feature flag. When provided it
    is bridged into the pipeline's ``BaseLoader`` slot via
    :class:`DocumentParserLoader`; when omitted (default) the existing
    ``LoaderRegistry`` (PDF + Markdown) is used — keeping the legacy path
    unchanged and runnable at all times (plan §8 Phase 1).
    """
    db = Path(data_dir) / "db"
    img = Path(data_dir) / "images"
    db.mkdir(parents=True, exist_ok=True)
    img.mkdir(parents=True, exist_ok=True)

    integrity = SQLiteIntegrityChecker(str(db / "ingestion_history.db"))
    image_storage = ImageStorage(
        db_path=str(db / "image_index.db"),
        base_dir=str(img),
    )
    if document_parser is not None:
        # M5: a unified DocumentParser behind the feature flag fronts the same
        # single ``loader`` slot the pipeline always had.
        from src.document_parser.loader_adapter import DocumentParserLoader
        loader = DocumentParserLoader(document_parser)
    else:
        # M5: one registry dispatches per file extension (PDF / Markdown), so
        # a mixed-format batch flows through a single pipeline loader slot.
        loader = LoaderRegistry.from_settings(
            image_dir=str(img),
            image_classifier=settings.ingestion.image_classifier,
        )
    chunker = DocumentChunker(splitter)

    # Transforms — both run rule-only when llm is None; if the user
    # wired an LLM into the build, the transforms will pick it up
    # via the settings (use_llm flag controls).
    chunk_refiner = ChunkRefiner(
        settings.ingestion.chunk_refiner, llm=llm,
    )
    image_classifier = ImageContentClassifier(
        settings.ingestion.image_classifier, llm=llm,
    )
    image_captioner = ImageCaptioner(
        settings.ingestion.image_captioner, llm=llm,
    )
    metadata_enricher = MetadataEnricher(
        settings.ingestion.metadata_enricher, llm=llm,
    )

    sparse_encoder = SparseEncoder.from_settings(settings.sparse)
    batch = BatchProcessor(
        dense_encoder=DenseEncoder(embedding),
        sparse_encoder=sparse_encoder,
        batch_size=32,
    )
    upserter = VectorUpserter(vector_store)
    bm25 = BM25Indexer(
        persist_dir=str(db / "bm25"),
        sparse_encoder=sparse_encoder,
    )
    return IngestionPipeline(
        loader=loader,
        chunker=chunker,
        transforms=[
            chunk_refiner,
            image_classifier,   # classify before captioning to save LLM calls
            image_captioner,
            metadata_enricher,
        ],
        batch_processor=batch,
        vector_upserter=upserter,
        bm25_indexer=bm25,
        file_integrity=integrity,
        image_storage=image_storage,
        bm25_index_name=collection,
        # ``collection`` drives the per-run scoping of the integrity /
        # image writes (BM25 already carries the name above). Without it
        # a per-collection pipeline silently labels those stores
        # "default" while the BM25 index goes to the right collection.
        collection=collection,
    )


# ---------------------------------------------------------------------------
# File enumeration
# ---------------------------------------------------------------------------

def iter_input_files(path: str) -> Iterable[Path]:
    """Yield every supported file under ``path`` (file or directory)."""
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() in SUPPORTED_EXTS:
            yield p
        return
    if p.is_dir():
        for ext in SUPPORTED_EXTS:
            yield from sorted(p.rglob(f"*{ext}"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ingest",
        description=(
            "Ingest documents into the knowledge hub. "
            "Each file is parsed, chunked, transformed, encoded, "
            "and stored (vector + BM25 + images)."
        ),
    )
    parser.add_argument(
        "--path", required=True,
        help="File or directory to ingest. Supported: .pdf, .md, .markdown",
    )
    parser.add_argument(
        "--collection", default="default",
        help="Collection name (used as BM25 index name and image "
             "subdirectory). Default: 'default'",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-ingest even if the file's SHA256 is already marked "
             "as success in the integrity DB.",
    )
    parser.add_argument(
        "--data-dir", default="./data",
        help="Root data directory (default: ./data). The script "
             "creates <data-dir>/db/ and <data-dir>/images/ if "
             "they don't exist.",
    )
    parser.add_argument(
        "--config", default="./config/settings.yaml",
        help="Path to settings.yaml (default: ./config/settings.yaml).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable INFO-level logging on stderr.",
    )
    return parser.parse_args(argv)


def _force_clear(integrity: SQLiteIntegrityChecker, file_hash: str) -> None:
    """Forget the file so the next run will re-ingest it."""
    if integrity.forget(file_hash):
        logger.info("--force: cleared integrity row for %s", file_hash[:12])


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    # Load settings — fall back to defaults if the file is missing
    # so the script can be exercised against injected backends.
    config_path = Path(args.config)
    if config_path.is_file():
        settings = load_settings(str(config_path))
    else:
        logger.warning(
            "Settings file %s not found — using defaults. "
            "Backends will use default providers (may require API keys).",
            config_path,
        )
        settings = Settings()

    # Defer heavy backend imports so the script's --help works
    # even if an optional provider package is missing.
    from src.libs.embedding import EmbeddingFactory
    from src.libs.llm import LLMFactory
    from src.libs.splitter import SplitterFactory
    from src.libs.vector_store import VectorStoreFactory

    splitter = SplitterFactory.create(settings.splitter)
    embedding = EmbeddingFactory.create(settings.embedding)
    vector_store = VectorStoreFactory.create(settings.vector_store)
    llm = (
        LLMFactory.create(settings.llm) if settings.llm else None
    )

    pipeline = build_pipeline(
        settings=settings,
        data_dir=args.data_dir,
        collection=args.collection,
        splitter=splitter,
        embedding=embedding,
        vector_store=vector_store,
        llm=llm,
    )
    # Wrap the pipeline in the application service so the CLI shares the
    # same stable dependency as MCP / Streamlit / Web API (M1 thin facade).
    from src.application.services import IngestionService
    ingestion_service = IngestionService(pipeline)

    files = list(iter_input_files(args.path))
    if not files:
        print(
            f"[ingest] No supported files found at {args.path!r} "
            f"(extensions: {', '.join(SUPPORTED_EXTS)})",
            file=sys.stderr,
        )
        return 1

    # --force: clear integrity rows for every target file up front
    if args.force:
        for p in files:
            try:
                h = ingestion_service.pipeline.file_integrity.compute_sha256(str(p))
                _force_clear(ingestion_service.pipeline.file_integrity, h)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not pre-clear %s: %s", p, exc,
                )

    # Process each file. We stop on the first hard error so the
    # operator sees it clearly; transient issues are reported as
    # warnings inside the pipeline.
    n_ok = n_skip = 0
    for p in files:
        try:
            result = ingestion_service.ingest(str(p))
        except Exception as exc:  # noqa: BLE001
            print(
                f"[ingest] FAIL {p.name}: {exc}",
                file=sys.stderr,
            )
            return 2
        if result.skipped:
            n_skip += 1
            tag = "SKIP"
        else:
            n_ok += 1
            tag = "OK  "
        print(
            f"[ingest] {tag} {p.name}  "
            f"chunks={result.n_chunks} "
            f"images={result.n_images_saved} "
            f"vector_upserts={result.n_records_upserted} "
            f"bm25_docs={result.bm25_n_docs}"
        )

    print(
        f"[ingest] Done. ingested={n_ok} skipped={n_skip} "
        f"data_dir={args.data_dir} collection={args.collection}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
