"""
SparseEncoder (C9) — BM25 term-weight computation for chunks.

For every chunk, produces a ``{term: weight}`` mapping suitable for
the ``ChunkRecord.sparse_vector`` field. The actual inverted index
is built later by C11 (``BM25Indexer``); this class only computes
the per-chunk weights.

Algorithm
---------
BM25 (Okapi) per-term weight for a single document is::

    idf(t)   = log((N - df(t) + 0.5) / (df(t) + 0.5) + 1)
    tf_norm  = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))
    weight   = idf(t) * tf_norm

Where ``N`` is total documents, ``df(t)`` is the number of documents
containing term ``t``, ``dl`` is this document's term count, and
``avgdl`` is the corpus average. The constants ``k1`` and ``b`` are
configurable; the standard defaults are ``k1=1.5``, ``b=0.75``.

Because idf requires corpus-wide statistics, the encoder makes two
passes: first tokenize every chunk to gather ``df`` + ``avgdl``,
then weight each chunk using those stats. This is done inside a
single ``encode()`` call so the caller doesn't have to think about
fit/transform stages.

Tokenization
------------
- ASCII words: runs of ``[A-Za-z0-9]+`` (lowercased if
  ``lowercase=True``).
- CJK characters: each character is a separate unigram (so
  "你好" → ["你", "好"]), which is the standard approach for
  Chinese without a dedicated segmenter.
- Pure-digit tokens are dropped by default (they don't carry
  semantic weight in BM25).
- ``stopwords`` (frozenset | None) optionally filtered.

Empty / whitespace-only input produces an empty ``{}`` sparse
vector — never ``None`` — so downstream indexers don't need a
None-check.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Iterable

from src.core.types import Chunk, ChunkRecord

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext


# Tokenizer patterns
#
# Match runs of ASCII alphanumerics OR a single CJK character. We
# intentionally allow 1-char ASCII matches (e.g. "z", "I") because
# the ``stopwords`` + ``min_term_len`` filters downstream decide
# what to keep; using a stricter regex here would silently drop
# tokens the user might want to keep (and breaks ``drop_pure_digits``,
# which needs the regex to MATCH pure-digit runs so the filter can
# then choose to drop them).
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")

# Single CJK character (one char from the unified CJK blocks).
_CJK_CHAR_RE = re.compile(r"[一-鿿]")

# Default stopwords (English + a few common CJK particles). Matches
# the C6 enricher so tags / sparse vectors agree on what to ignore.
_DEFAULT_STOPWORDS = frozenset(
    """
    a an the and or but if then else when while for to of in on at by
    with from as is are was were be been being have has had do does did
    this that these those it its their there here i you he she we they
    me him her us them my your our their what which who whom whose
    not no nor so too very can could may might will would shall should
    的 了 是 在 有 和 与 或 但 如果 那么 这 那 我 你 他 她 它 我们 你们
    他们 也 都 而 及 等 于 上 下 中 一 个 不 来 去 出 到 没
    """.split()
)


# N-gram size for the CJK side of tokenization. 1+2-gram = emit every
# individual CJK char AND every adjacent pair. This gives enough
# word-boundary signal for short Chinese terms ("考勤", "加班") without
# pulling in a segmentation dictionary like jieba.
_CJK_NGRAM_MAX = 2


class SparseEncoder:
    """
    Compute BM25 term weights for chunks.

    Tokenization: ASCII alphanumeric runs are kept as single tokens;
    CJK text is tokenized as character n-grams of size 1 and 2
    (1+2-gram). The bigrams only form within contiguous CJK runs, so
    "员工abc请假" yields the bigrams 员工/工请/请假 but never mixes
    CJK with ASCII.

    The 1+2-gram scheme beats pure unigrams on Chinese BM25 because
    adjacent-character pairs carry word-boundary signal that single
    characters don't — "考勤" is a frequent two-character term in
    corporate policy docs that would otherwise be split into "考"
    and "勤" and drown in unrelated "考核" / "勤奋" matches. We
    deliberately skip longer n-grams (3+) because for a small /
    domain-specific corpus the extra terms add index weight without
    much recall benefit.

    Args:
        k1: BM25 saturation parameter. Higher = term-frequency
            matters more. Default 1.5 (standard).
        b: BM25 document-length normalization. 0 = no length
            normalization, 1 = full. Default 0.75 (standard).
        lowercase: Lowercase ASCII tokens before counting. Default
            True (BM25 is case-insensitive in practice).
        stopwords: Iterable of terms to drop. ``None`` uses the
            built-in English+Chinese stopword list. Pass an empty
            set to disable filtering.
        min_term_len: Drop tokens shorter than this. Default 1
            (allow CJK unigrams and bigrams). Raise to 2 to drop
            single ASCII chars and CJK unigrams, keeping only
            bigrams and longer.
        drop_pure_digits: Drop tokens that are pure digits (e.g.
            "2024"). Default True.
    """

    name = "sparse_encoder"

    def __init__(
        self,
        k1: float = 1.5,
        b: float = 0.75,
        lowercase: bool = True,
        stopwords: Iterable[str] | None = None,
        min_term_len: int = 1,
        drop_pure_digits: bool = True,
    ) -> None:
        if k1 < 0:
            raise ValueError(f"k1 must be >= 0, got {k1}")
        if not 0 <= b <= 1:
            raise ValueError(f"b must be in [0, 1], got {b}")
        if min_term_len < 1:
            raise ValueError(f"min_term_len must be >= 1, got {min_term_len}")

        self.k1 = k1
        self.b = b
        self.lowercase = lowercase
        self.stopwords = (
            frozenset(stopwords) if stopwords is not None
            else _DEFAULT_STOPWORDS
        )
        self.min_term_len = min_term_len
        self.drop_pure_digits = drop_pure_digits

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------
    @classmethod
    def from_settings(
        cls,
        settings: "SparseSettings | None" = None,
    ) -> "SparseEncoder":
        """
        Build a :class:`SparseEncoder` from the project's
        ``SparseSettings``.

        Kept as a single construction entry point so ingestion,
        query, and the dashboard can't drift apart on which stopword
        list / min-len / digit policy they apply. Currently the
        tokenizer is fixed (1+2-gram char n-gram) and the factory
        only forwards configuration flags — no segmentation choice
        — but the indirection makes it easy to add one later without
        touching every call site.
        """
        if settings is None:
            return cls()
        return cls(
            stopwords=settings.stopwords,
            min_term_len=settings.min_term_len,
            drop_pure_digits=settings.drop_pure_digits,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def encode(
        self,
        chunks: list[Chunk],
        trace: "TraceContext | None" = None,
    ) -> list[ChunkRecord]:
        if not chunks:
            return []

        if trace is not None:
            trace.record_stage(
                self.name,
                event="start",
                n_chunks=len(chunks),
            )

        # Pass 1: tokenize every chunk.
        token_lists: list[list[str]] = [self._tokenize(c.text) for c in chunks]

        # Corpus statistics.
        n_docs = len(token_lists)
        avgdl = sum(len(tl) for tl in token_lists) / n_docs
        df: Counter[str] = Counter()
        for tl in token_lists:
            for term in set(tl):  # set: each term counted once per doc
                df[term] += 1

        # Pass 2: compute BM25 weights per chunk.
        records: list[ChunkRecord] = []
        for chunk, tokens in zip(chunks, token_lists):
            sparse = self._bm25_weights(tokens, df, n_docs, avgdl)
            records.append(
                ChunkRecord.from_chunk(chunk, sparse_vector=sparse)
            )

        if trace is not None:
            trace.record_stage(
                self.name,
                event="finish",
                n_out=len(records),
                n_unique_terms=len(df),
            )
        return records

    # ------------------------------------------------------------------
    # Tokenization
    # ------------------------------------------------------------------
    def tokenize(self, text: str) -> list[str]:
        """Split text into normalized tokens. Order preserved.

        Public entry point so downstream components
        (e.g. ``BM25Indexer``) can reuse the exact same tokenization
        rules as the encoder. Each call is independent — no shared
        state — so the tokenizer is safe to use concurrently on
        disjoint chunk lists.
        """
        return self._tokenize(text)

    # ------------------------------------------------------------------
    # Tokenizer backend selection
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_tokenizer(tokenizer: str) -> str:
        """Validate and normalize the tokenizer backend name."""
        t = (tokenizer or TOKENIZER_DEFAULT).lower().strip()
        if t == TOKENIZER_JIEBA:
            try:
                import jieba  # noqa: F401
                return TOKENIZER_JIEBA
            except ImportError:  # pragma: no cover - optional dep
                logger.warning(
                    "jieba not installed; falling back to default tokenizer",
                )
                return TOKENIZER_DEFAULT
        if t != TOKENIZER_DEFAULT:
            logger.warning(
                "unknown tokenizer %r; falling back to default", tokenizer,
            )
            return TOKENIZER_DEFAULT
        return TOKENIZER_DEFAULT

    def _tokenize(self, text: str) -> list[str]:
        """Split text into normalized tokens. Order preserved.

        ASCII runs are kept as single tokens (lowercased if
        ``self.lowercase``). CJK runs are split into 1+2-gram
        character n-grams: each individual character, and each
        adjacent pair. Bigrams are only formed within a contiguous
        CJK run — a Latin character (or punctuation, or any
        character the regex doesn't match) breaks the run, so
        "员工abc请假" yields bigrams 员工/请假 but not 工请, and
        "考勤,管理" yields bigrams 考勤/管理 but not 勤管.

        Each call is independent — no shared state — so the encoder
        is safe to use concurrently on disjoint chunk lists.
        """
        if not text:
            return []

        out: list[str] = []
        cjk_run: list[str] = []  # pending CJK chars to flush as bigrams
        last_end = 0  # end position of the previous match in ``text``

        def _emit(tok: str) -> None:
            """Apply the standard filters and append a token."""
            if self.lowercase:
                tok = tok.lower()
            if self.drop_pure_digits and tok.isdigit():
                return
            if len(tok) < self.min_term_len:
                return
            if tok in self.stopwords:
                return
            out.append(tok)

        def _flush_cjk() -> None:
            """Emit bigrams from the pending CJK run, then clear it."""
            for i in range(len(cjk_run) - 1):
                _emit(cjk_run[i] + cjk_run[i + 1])
            cjk_run.clear()

        for m in _TOKEN_RE.finditer(text):
            # If the regex skipped any characters between the last
            # match and this one (punctuation, whitespace, fullwidth
            # symbols, etc.), the CJK run is broken — flush bigrams.
            if m.start() > last_end:
                _flush_cjk()
            last_end = m.end()

            tok = m.group(0)
            if _CJK_CHAR_RE.fullmatch(tok):
                # CJK char: append to current run, then emit as 1-gram.
                cjk_run.append(tok)
                _emit(tok)
            else:
                # ASCII run / digit run: flush any pending CJK
                # bigrams first, then emit this token.
                _flush_cjk()
                _emit(tok)

        # Trailing CJK run (no following token to flush it).
        _flush_cjk()
        return out

    # ------------------------------------------------------------------
    # BM25 math
    # ------------------------------------------------------------------
    def _bm25_weights(
        self,
        tokens: list[str],
        df: Counter[str],
        n_docs: int,
        avgdl: float,
    ) -> dict[str, float]:
        """Return ``{term: weight}`` for one document.

        Empty input → empty dict. Repeated terms in the document
        are accumulated via term frequency (``tf``).
        """
        if not tokens:
            return {}

        doc_len = len(tokens)
        tf: Counter[str] = Counter(tokens)
        weights: dict[str, float] = {}

        for term, f in tf.items():
            # idf: standard BM25+1 form (always non-negative).
            idf = math.log(
                (n_docs - df[term] + 0.5) / (df[term] + 0.5) + 1.0
            )
            # Length-normalized term frequency.
            denom = f + self.k1 * (1.0 - self.b + self.b * doc_len / avgdl)
            tf_norm = (f * (self.k1 + 1.0)) / denom if denom > 0 else 0.0
            w = idf * tf_norm
            if w > 0:
                weights[term] = w
        return weights
