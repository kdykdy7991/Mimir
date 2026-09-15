"""
Shared support for the offline retrieval Golden Set (Task 01.3/01.4).

This module is the single construction entry point used by BOTH the
fixture seeder (``scripts/seed_retrieval_fixtures.py``) and the offline
evaluation runner (``scripts/run_retrieval_eval.py``), so the index that
is built and the index that is queried can never be assembled by two
different code paths.

Contents
--------
* :class:`DeterministicHashEmbedding` — an offline, network-free embedding
  with fixed 512 dimensions, built by hashing the **same production
  token stream** (:class:`SparseEncoder` tokenization) into signed vector
  dimensions. It is NOT a neural semantic model: similarity geometry
  reflects token overlap. Real-model profiles (OpenAI-compatible
  embeddings on the internal vLLM endpoint, cross-encoder reranking) are
  separate, explicitly invoked run profiles in 01.5.
* Golden corpus loading / id derivation — chunk ids reuse the production
  chunker formula (``DocumentChunker._generate_chunk_id``) and document
  ids reuse :func:`document_uuid`, so fixture ids are the same shape and
  derivation as real ingestion.
* :func:`build_seed_components` — real Chroma + real BM25 indexes in an
  arbitrary data directory, plus a deterministic build manifest.

Nothing here imports Web/MCP layers and no network is used.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.application.identifiers import document_uuid
from src.core.settings import VectorStoreSettings
from src.core.types import ChunkRecord
from src.ingestion.chunking.document_chunker import DocumentChunker
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage import BM25Indexer
from src.ingestion.storage.vector_upserter import VectorUpserter
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.vector_store.chroma_store import ChromaStore

# ---------------------------------------------------------------------------
# Constants — shared by seeder, evaluator and tests
# ---------------------------------------------------------------------------

GOLDEN_COLLECTION = "golden_v1"
CORPUS_SCHEMA_VERSION = "golden-corpus-v1"
CASES_SCHEMA_VERSION = "retrieval-golden-v1"

# Bump when the corpus text changes; baseline snapshots are keyed to it.
CORPUS_REVISION = "2026-09-15.v1"

# Versioned profile of the deterministic embedding. Changing the hash
# scheme or dimensions is a profile change that invalidates baselines.
EMBED_PROFILE = "deterministic-hash-v1"
EMBED_DIMENSIONS = 512

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "retrieval_golden"
CORPUS_PATH = FIXTURE_DIR / "corpus.json"
CASES_PATH = FIXTURE_DIR / "cases.jsonl"
CASE_SCHEMA_PATH = FIXTURE_DIR / "schema.json"
DEFAULT_DATA_DIR = FIXTURE_DIR / "data"
MANIFEST_NAME = "build_manifest.json"


# ---------------------------------------------------------------------------
# Deterministic offline embedding
# ---------------------------------------------------------------------------

class DeterministicHashEmbedding(BaseEmbedding):
    """Token-hash bag embedding for offline retrieval evaluation.

    Each production token (ASCII run lowercased, CJK 1+2-gram, stopwords
    and pure-digit tokens already removed by :class:`SparseEncoder`) maps
    to a fixed vector dimension via BLAKE2b; one byte of the same hash
    chooses the sign. Documents/queries that share tokens share vector
    dimensions — the geometry is lexical-overlap geometry, and it is
    bit-stable across processes and Python hash seeds.
    """

    provider_name = EMBED_PROFILE

    def __init__(
        self,
        dimensions: int = EMBED_DIMENSIONS,
        sparse_encoder: SparseEncoder | None = None,
    ) -> None:
        super().__init__()
        self._dimensions = dimensions
        self._encoder = sparse_encoder or SparseEncoder()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @staticmethod
    def _hash_token(token: str) -> tuple[int, int]:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        dimension = int.from_bytes(digest[:4], "big")
        sign_byte = digest[4]
        return dimension, sign_byte

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in self._encoder.tokenize(text):
            dimension, sign_byte = self._hash_token(token)
            vector[dimension % self._dimensions] += 1.0 if sign_byte < 128 else -1.0
        norm = sum(v * v for v in vector) ** 0.5
        if norm > 0:
            vector = [v / norm for v in vector]
        return vector

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [self._embed_one(t or "") for t in texts]

    def embed_single(self, text: str, **kwargs: Any) -> list[float]:
        return self._embed_one(text or "")


# ---------------------------------------------------------------------------
# Corpus loading + id derivation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GoldenChunk:
    source_path: str
    document_id: str
    title: str
    doc_type: str
    chunk_index: int
    ref: str
    text: str
    page: int | None
    content_type: str | None


def load_corpus(path: Path | str = CORPUS_PATH) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != CORPUS_SCHEMA_VERSION:
        raise ValueError(
            f"unexpected corpus schema_version: {data.get('schema_version')!r} "
            f"(want {CORPUS_SCHEMA_VERSION!r})"
        )
    if data.get("collection") != GOLDEN_COLLECTION:
        raise ValueError(f"unexpected golden collection: {data.get('collection')!r}")
    return data


def corpus_chunks(corpus: dict[str, Any]) -> list[GoldenChunk]:
    """Flatten the authored corpus into ordered chunk descriptors and
    derive document/chunk ids with the production id functions."""
    chunks: list[GoldenChunk] = []
    for document in corpus["documents"]:
        source_path = document["source_path"]
        doc_id = str(document_uuid(GOLDEN_COLLECTION, source_path))
        for spec in document["chunks"]:
            text = spec["text"]
            chunk_index = int(spec["index"])
            chunk_id = DocumentChunker._generate_chunk_id(
                doc_id, chunk_index, text,
            )
            chunks.append(
                GoldenChunk(
                    source_path=source_path,
                    document_id=doc_id,
                    title=document["title"],
                    doc_type=document["doc_type"],
                    chunk_index=chunk_index,
                    # ``ref`` is the human-facing stable slug; production
                    # retrieval ids are still the content-addressed ids.
                    ref=str(spec["ref"]),
                    text=text,
                    page=spec.get("page"),
                    content_type=spec.get("content_type"),
                ),
            )
    return chunks


def chunk_records(chunks: list[GoldenChunk]) -> list[ChunkRecord]:
    """Build storage ChunkRecords with production-shaped metadata.

    Metadata mirrors what :class:`DocumentChunker` inheritance produces:
    document-level fields (source_path/title/doc_type) plus
    ``chunk_index`` and optional extraction markers (page/content_type).
    """
    records: list[ChunkRecord] = []
    for c in chunks:
        metadata: dict[str, Any] = {
            "source_path": c.source_path,
            "title": c.title,
            "doc_type": c.doc_type,
            "chunk_index": c.chunk_index,
            "chunk_ref": c.ref,
            "collection": GOLDEN_COLLECTION,
        }
        if c.page is not None:
            metadata["page"] = c.page
        if c.content_type:
            metadata["content_type"] = c.content_type
        # Production formula; VectorUpserter keeps record.id verbatim.
        chunk_id = DocumentChunker._generate_chunk_id(
            c.document_id, c.chunk_index, c.text,
        )
        records.append(
            ChunkRecord(
                id=chunk_id,
                text=c.text,
                metadata=metadata,
                source_ref=c.document_id,
            ),
        )
    return records


# ---------------------------------------------------------------------------
# Deterministic seeding (real Chroma + real BM25)
# ---------------------------------------------------------------------------

def _chromadb_version() -> str:
    try:
        import chromadb
    except ImportError:
        return "not-installed"
    return getattr(chromadb, "__version__", "unknown")


def build_manifest(corpus: dict[str, Any], chunks: list[GoldenChunk]) -> dict[str, Any]:
    """Manifest content is a pure function of the corpus + profiles —
    no timestamps, no machine names, so two rebuilds compare byte-equal."""
    return {
        "schema_version": "golden-build-manifest-v1",
        "corpus_revision": CORPUS_REVISION,
        "corpus_sha256": hashlib.sha256(
            json.dumps(corpus, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ).hexdigest(),
        "collection": GOLDEN_COLLECTION,
        "embedding_profile": EMBED_PROFILE,
        "embedding_dimensions": EMBED_DIMENSIONS,
        "bm25": {"k1": 1.5, "b": 0.75},
        "fusion": "rrf-k60",
        "chromadb_version_at_build": _chromadb_version(),
        "document_count": len(corpus["documents"]),
        "chunk_count": len(chunks),
        "documents": [
            {
                "source_path": d["source_path"],
                "document_id": str(
                    document_uuid(GOLDEN_COLLECTION, d["source_path"]),
                ),
                "title": d["title"],
                "doc_type": d["doc_type"],
            }
            for d in corpus["documents"]
        ],
        "chunks": [
            {
                "chunk_id": DocumentChunker._generate_chunk_id(
                    c.document_id, c.chunk_index, c.text,
                ),
                "ref": c.ref,
                "source_path": c.source_path,
                "document_id": c.document_id,
                "chunk_index": c.chunk_index,
                "page": c.page,
                "content_type": c.content_type,
                "text_sha256": hashlib.sha256(c.text.encode("utf-8")).hexdigest(),
            }
            for c in chunks
        ],
    }


def seed_fixture_data(
    data_dir: Path | str,
    *,
    corpus_path: Path | str = CORPUS_PATH,
    force: bool = False,
) -> dict[str, Any]:
    """Build Chroma + BM25 indexes for the golden corpus into ``data_dir``.

    Layout matches the production query composition root:
    ``<data_dir>/db/chroma`` and ``<data_dir>/db/bm25/<collection>.json``.

    Refuses to populate a non-empty directory unless ``force`` is given.
    Returns the build manifest.
    """
    data_dir = Path(data_dir)
    if data_dir.exists() and any(data_dir.rglob("*")):
        if not force:
            raise FileExistsError(
                f"golden data dir is not empty: {data_dir} (use force=True to rebuild)",
            )

    corpus = load_corpus(corpus_path)
    golden = corpus_chunks(corpus)
    records = chunk_records(golden)

    # Deterministic dense vectors.
    embedding = DeterministicHashEmbedding()
    vectors = embedding.embed([r.text for r in records])
    for record, vector in zip(records, vectors):
        record.dense_vector = vector

    # Real Chroma persistent store, production settings shape.
    store = ChromaStore(
        VectorStoreSettings(
            backend="chroma",
            persist_path=str(data_dir / "db" / "chroma"),
            collection_name=GOLDEN_COLLECTION,
        ),
    )
    upserted = VectorUpserter(store).upsert(records)
    if upserted != len(records):
        raise RuntimeError(
            f"vector upsert count mismatch: {upserted} != {len(records)}",
        )

    # Real BM25 index at the production-relative path.
    bm25 = BM25Indexer(
        persist_dir=str(data_dir / "db" / "bm25"),
        sparse_encoder=SparseEncoder(),
    )
    index = bm25.build(records)
    bm25.save(index, GOLDEN_COLLECTION)

    manifest = build_manifest(corpus, golden)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "GOLDEN_COLLECTION",
    "CORPUS_SCHEMA_VERSION",
    "CASES_SCHEMA_VERSION",
    "CORPUS_REVISION",
    "EMBED_PROFILE",
    "EMBED_DIMENSIONS",
    "FIXTURE_DIR",
    "CORPUS_PATH",
    "CASES_PATH",
    "CASE_SCHEMA_PATH",
    "DEFAULT_DATA_DIR",
    "MANIFEST_NAME",
    "DeterministicHashEmbedding",
    "GoldenChunk",
    "load_corpus",
    "corpus_chunks",
    "chunk_records",
    "build_manifest",
    "seed_fixture_data",
]
