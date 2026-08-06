"""
BM25Indexer (C11) — corpus-level inverted index for BM25 retrieval.

Reads ``ChunkRecord`` objects (output of :class:`SparseEncoder`) and
produces an in-memory inverted index of the form::

    {
      "<term>": {
        "idf": float,
        "postings": [
          {"chunk_id": str, "tf": int, "doc_length": int},
          ...
        ]
      },
      ...
    }

Persists the index to JSON under ``data/db/bm25/<name>.json`` and
can re-hydrate it for querying. The query path uses the standard
BM25 Okapi scoring formula::

    score(t, d) = idf(t) *
                  (tf(t,d) * (k1 + 1)) /
                  (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))

IDF uses the ``+1`` (Lucene / BM25+) form::

    idf(t) = log((N - df(t) + 0.5) / (df(t) + 0.5) + 1)

This is the same formula :class:`SparseEncoder` uses when writing
per-chunk ``sparse_vector`` weights, so index-time and query-time
term weighting agree. It is also always non-negative: the original
BM25 form (without ``+1``) goes negative for any term appearing in
more than half the corpus, which makes a common-term match *lower*
a document's score. The ``+1`` form still ranks rare terms above
common ones, it just keeps the floor at zero.

Design notes
------------
* The index is split into a pure-data structure (``BM25Index``) and
  a persistence/IO layer (``BM25Indexer``). This lets D3's
  ``SparseRetriever`` work with the index without going through
  disk.
* Tokenization is delegated to a :class:`SparseEncoder` instance
  injected at construction time, so the indexer uses exactly the
  same tokens the encoder wrote into each chunk's ``sparse_vector``.
  Different corpora can use different tokenization rules by
  swapping the encoder.
* Both full rebuild (``build``) and incremental add (``add``) are
  supported, satisfying the spec's "重建与增量更新" requirement.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.types import ChunkRecord
from src.ingestion.embedding.sparse_encoder import SparseEncoder

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext


logger = logging.getLogger(__name__)


# Default persistence location, matching the spec.
DEFAULT_PERSIST_DIR = "./data/db/bm25"

# BM25 scoring constants (standard Okapi defaults).
_DEFAULT_K1 = 1.5
_DEFAULT_B = 0.75


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Posting:
    """One term occurrence in one document."""
    chunk_id: str
    tf: int
    doc_length: int


@dataclass
class TermEntry:
    """One term's idf + sorted posting list."""
    idf: float
    postings: list[Posting] = field(default_factory=list)


@dataclass
class ScoredDoc:
    """Result of one query — a chunk's BM25 score for the query."""
    chunk_id: str
    score: float


@dataclass
class BM25Index:
    """
    In-memory BM25 inverted index.

    Attributes:
        n_docs: Total documents in the corpus.
        avgdl: Average document length (in tokens).
        terms: term -> TermEntry(idf, postings).
        k1, b: BM25 constants (kept here so the index is self-
            describing for query).
    """
    n_docs: int = 0
    avgdl: float = 0.0
    terms: dict[str, TermEntry] = field(default_factory=dict)
    k1: float = _DEFAULT_K1
    b: float = _DEFAULT_B

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------
    def doc_length(self, chunk_id: str) -> int | None:
        """Look up the doc_length for a chunk by scanning its postings.
        O(corpus_size) but only used in tests + small admin scripts.
        """
        for entry in self.terms.values():
            for p in entry.postings:
                if p.chunk_id == chunk_id:
                    return p.doc_length
        return None


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

class BM25Indexer:
    """
    Build, save, load, and query a BM25 inverted index.

    Args:
        persist_dir: Where to write ``<name>.json`` files. Created
            on first save if it doesn't exist.
        sparse_encoder: A :class:`SparseEncoder` whose ``tokenize``
            method defines the tokenization rules. The same encoder
            that produced the chunk's ``sparse_vector`` should be
            used here, so the postings' tf matches the weights.
        k1, b: BM25 scoring constants.
    """

    name = "bm25_indexer"

    def __init__(
        self,
        persist_dir: str = DEFAULT_PERSIST_DIR,
        sparse_encoder: SparseEncoder | None = None,
        k1: float = _DEFAULT_K1,
        b: float = _DEFAULT_B,
    ) -> None:
        if k1 < 0:
            raise ValueError(f"k1 must be >= 0, got {k1}")
        if not 0 <= b <= 1:
            raise ValueError(f"b must be in [0, 1], got {b}")
        self.persist_dir = Path(persist_dir)
        self.tokenizer = sparse_encoder or SparseEncoder()
        self.k1 = k1
        self.b = b

    # ------------------------------------------------------------------
    # BM25 math
    # ------------------------------------------------------------------
    @staticmethod
    def _idf(n_docs: int, df_t: int) -> float:
        """
        Inverse document frequency, ``+1`` (Lucene / BM25+) form::

            log((N - df + 0.5) / (df + 0.5) + 1)

        Always non-negative, and identical to the formula
        :class:`SparseEncoder` uses for per-chunk weights, so
        index-time and query-time weighting agree.
        """
        return math.log((n_docs - df_t + 0.5) / (df_t + 0.5) + 1.0)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    def build(
        self,
        records: list[ChunkRecord],
        trace: "TraceContext | None" = None,
    ) -> BM25Index:
        """Build a fresh index from ``records`` (full rebuild)."""
        if trace is not None:
            trace.record_stage(
                self.name, event="build_start", n_records=len(records),
            )

        index = BM25Index(k1=self.k1, b=self.b)
        # Tokenize every chunk once; record (chunk_id, tokens, doc_len).
        tokenized: list[tuple[str, list[str], int]] = []
        for rec in records:
            tokens = self.tokenizer.tokenize(rec.text)
            tokenized.append((rec.id, tokens, len(tokens)))

        index.n_docs = len(tokenized)
        index.avgdl = (
            sum(dl for _, _, dl in tokenized) / index.n_docs
            if index.n_docs else 0.0
        )

        # df: term -> number of docs containing it
        df: Counter[str] = Counter()
        for _, tokens, _ in tokenized:
            for term in set(tokens):
                df[term] += 1

        # postings: term -> list of (chunk_id, tf, doc_length)
        postings_map: dict[str, list[Posting]] = {}
        tf_map: dict[tuple[str, str], int] = {}
        for chunk_id, tokens, dl in tokenized:
            tf: Counter[str] = Counter(tokens)
            for term, f in tf.items():
                tf_map[(term, chunk_id)] = f
                postings_map.setdefault(term, []).append(
                    Posting(chunk_id=chunk_id, tf=f, doc_length=dl)
                )

        # idf + assembly
        for term, df_t in df.items():
            index.terms[term] = TermEntry(
                idf=self._idf(index.n_docs, df_t),
                postings=sorted(
                    postings_map[term], key=lambda p: p.chunk_id
                ),
            )

        if trace is not None:
            trace.record_stage(
                self.name, event="build_finish",
                n_docs=index.n_docs,
                n_unique_terms=len(index.terms),
                avgdl=index.avgdl,
            )
        return index

    # ------------------------------------------------------------------
    # Incremental update
    # ------------------------------------------------------------------
    def add(
        self,
        index: BM25Index,
        records: list[ChunkRecord],
        trace: "TraceContext | None" = None,
    ) -> BM25Index:
        """
        Add new records to an existing index, updating df / idf /
        avgdl. Documents whose ``id`` is already in the index are
        re-tokenized and re-posted (latest version wins).
        """
        if not records:
            return index

        # Remove postings for any chunk_id that's about to be re-added.
        incoming_ids = {r.id for r in records}
        for term, entry in index.terms.items():
            entry.postings = [
                p for p in entry.postings if p.chunk_id not in incoming_ids
            ]

        # Re-tokenize + collect per-chunk tf + dl.
        tokenized: list[tuple[str, list[str], int]] = []
        for rec in records:
            tokens = self.tokenizer.tokenize(rec.text)
            tokenized.append((rec.id, tokens, len(tokens)))

        # Compute the OLD total length so we can recompute avgdl.
        # We use the sum of all unique chunk doc_lengths stored in
        # postings — works because every chunk has at least one
        # term in the index after the first build (or it has none
        # and is skipped).
        seen_ids: set[str] = set()
        old_total_len = 0
        old_n = 0
        for entry in index.terms.values():
            for p in entry.postings:
                if p.chunk_id in seen_ids:
                    continue
                seen_ids.add(p.chunk_id)
                old_total_len += p.doc_length
                old_n += 1

        # For chunks that are re-added: drop their old length
        # contribution and add the new one.
        old_total_len_kept = old_total_len
        for chunk_id, _, _ in tokenized:
            if chunk_id in seen_ids:
                # subtract old length (we need to know it)
                # Cheat: keep a mapping of id -> old dl.
                pass
        # Simpler approach: re-derive everything from the union of
        # old postings (post removal) + new postings.
        # Build a map chunk_id -> doc_length from the now-clean index.
        dl_by_id: dict[str, int] = {}
        for entry in index.terms.values():
            for p in entry.postings:
                dl_by_id[p.chunk_id] = p.doc_length
        # Add new docs' lengths (overwriting any dup ids that we
        # already cleaned out).
        for chunk_id, _, dl in tokenized:
            dl_by_id[chunk_id] = dl

        new_n = len(dl_by_id)
        new_total = sum(dl_by_id.values())
        index.n_docs = new_n
        index.avgdl = new_total / new_n if new_n else 0.0

        # df recompute from cleaned postings + new chunks.
        df: Counter[str] = Counter()
        for term, entry in index.terms.items():
            df[term] = len(entry.postings)

        # Insert new postings.
        for chunk_id, tokens, dl in tokenized:
            tf: Counter[str] = Counter(tokens)
            for term, f in tf.items():
                if term not in index.terms:
                    index.terms[term] = TermEntry(idf=0.0, postings=[])
                entry = index.terms[term]
                entry.postings.append(
                    Posting(chunk_id=chunk_id, tf=f, doc_length=dl)
                )
                df[term] += 1

        # Recompute idf for every term whose df changed.
        for term, df_t in df.items():
            index.terms[term].idf = self._idf(index.n_docs, df_t)

        # Drop terms with no postings (shouldn't happen but be safe).
        empty_terms = [t for t, e in index.terms.items() if not e.postings]
        for t in empty_terms:
            del index.terms[t]

        # Sort postings for stable iteration.
        for entry in index.terms.values():
            entry.postings.sort(key=lambda p: p.chunk_id)

        if trace is not None:
            trace.record_stage(
                self.name, event="add_finish",
                added=len(records),
                n_docs=index.n_docs,
            )
        return index

    def remove_documents(
        self,
        index: BM25Index,
        chunk_ids: list[str],
        trace: "TraceContext | None" = None,
    ) -> BM25Index:
        """
        Remove a batch of chunks from the index by their chunk_id.

        Used by :class:`DocumentManager.delete_document` —
        callers first resolve the chunk_ids to remove via the
        vector store's ``get_by_metadata({"source_path": ...})``,
        then call this to keep the BM25 side in sync.

        Idempotent: chunk_ids that aren't in the index are
        silently skipped.

        Recomputes ``n_docs``, ``avgdl``, and per-term ``idf`` so
        downstream queries score correctly.
        """
        if not chunk_ids:
            return index
        remove_set = set(chunk_ids)

        # Drop the postings for these chunk_ids from every term.
        affected_terms: set[str] = set()
        for term, entry in index.terms.items():
            new_postings = [
                p for p in entry.postings
                if p.chunk_id not in remove_set
            ]
            if len(new_postings) != len(entry.postings):
                affected_terms.add(term)
            entry.postings = new_postings

        # Recompute chunk_id -> doc_length from surviving postings.
        dl_by_id: dict[str, int] = {}
        for entry in index.terms.values():
            for p in entry.postings:
                dl_by_id[p.chunk_id] = p.doc_length

        new_n = len(dl_by_id)
        new_total = sum(dl_by_id.values())
        index.n_docs = new_n
        index.avgdl = new_total / new_n if new_n else 0.0

        # Drop terms that no longer have any postings.
        empty_terms = [t for t in affected_terms if not index.terms[t].postings]
        for t in empty_terms:
            del index.terms[t]

        # Recompute idf for every term whose df changed.
        for term in affected_terms:
            if term not in index.terms:
                continue
            df_t = len(index.terms[term].postings)
            index.terms[term].idf = self._idf(index.n_docs, df_t)

        # Sort postings for stable iteration.
        for entry in index.terms.values():
            entry.postings.sort(key=lambda p: p.chunk_id)

        if trace is not None:
            trace.record_stage(
                self.name, event="remove_finish",
                removed=len(chunk_ids),
                n_docs=index.n_docs,
            )
        return index

    # ------------------------------------------------------------------
    # Query (BM25 Okapi)
    # ------------------------------------------------------------------
    def query(
        self,
        index: BM25Index,
        query: str | list[str],
        top_k: int = 10,
    ) -> list[ScoredDoc]:
        """
        Score every document against ``query`` and return the top-k
        by score (descending). Accepts a raw string (which is then
        tokenized) or a pre-tokenized list.
        """
        if isinstance(query, str):
            tokens = self.tokenizer.tokenize(query)
        else:
            tokens = list(query)
        if not tokens or not index.terms:
            return []

        # Count query term frequencies once.
        qtf: Counter[str] = Counter(tokens)
        # For each query term, iterate its postings and accumulate
        # BM25 scores per chunk.
        scores: dict[str, float] = {}
        for term, f_q in qtf.items():
            entry = index.terms.get(term)
            if entry is None:
                continue
            idf = entry.idf
            for p in entry.postings:
                denom = p.tf + self.k1 * (
                    1.0 - self.b + self.b * p.doc_length / index.avgdl
                ) if index.avgdl > 0 else (p.tf + self.k1)
                tf_norm = (p.tf * (self.k1 + 1.0)) / denom if denom > 0 else 0.0
                s = idf * tf_norm * f_q
                if s != 0:
                    scores[p.chunk_id] = scores.get(p.chunk_id, 0.0) + s

        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        return [
            ScoredDoc(chunk_id=cid, score=sc)
            for cid, sc in ranked[:top_k]
        ]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save(
        self,
        index: BM25Index,
        name: str = "corpus",
        trace: "TraceContext | None" = None,
    ) -> Path:
        """Write the index to ``<persist_dir>/<name>.json``.

        The write is **atomic** and uses a **unique temp file per call**:
        ``tempfile.mkstemp`` allocates a fresh ``<name>.json.<rand>.tmp``
        in the same directory, then ``os.replace`` moves it over the
        target. A crash mid-write never leaves a truncated index, and
        concurrent saves never share a temp filename (so multiple tasks
        can't clobber each other's temp file).
        """
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        path = self.persist_dir / f"{name}.json"
        payload = {
            "n_docs": index.n_docs,
            "avgdl": index.avgdl,
            "k1": index.k1,
            "b": index.b,
            "terms": {
                term: {
                    "idf": entry.idf,
                    "postings": [asdict(p) for p in entry.postings],
                }
                for term, entry in index.terms.items()
            },
        }
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.persist_dir),
            prefix=f"{name}.json.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            # os.replace is atomic on POSIX (same filesystem) and on
            # Windows when the target is on the same volume.
            os.replace(tmp_name, path)
        finally:
            # If the write or replace failed, don't leave the temp file.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        if trace is not None:
            trace.record_stage(self.name, event="save", path=str(path))
        return path

    def load(
        self,
        name: str = "corpus",
        trace: "TraceContext | None" = None,
    ) -> BM25Index:
        """Load the index from ``<persist_dir>/<name>.json``."""
        path = self.persist_dir / f"{name}.json"
        if not path.is_file():
            raise FileNotFoundError(f"BM25 index not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        index = BM25Index(
            n_docs=payload["n_docs"],
            avgdl=payload["avgdl"],
            k1=payload.get("k1", self.k1),
            b=payload.get("b", self.b),
        )
        for term, body in payload["terms"].items():
            index.terms[term] = TermEntry(
                idf=body["idf"],
                postings=[Posting(**p) for p in body["postings"]],
            )
        if trace is not None:
            trace.record_stage(self.name, event="load", path=str(path))
        return index
