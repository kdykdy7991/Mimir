"""
QueryProcessor (D1) — turns a raw user query into a structured
:class:`core.types.ProcessedQuery` for the downstream retrievers.

Two pieces:

1. **Keywords** — tokens from the query, lowercased, with the
   same stopword / digit / length rules the indexer used. Sharing
   the tokenizer with ``SparseEncoder`` keeps query terms
   aligned with the index's vocabulary, which is the only way
   BM25 (and any future bag-of-words retriever) will ever match.

2. **Filters** — left as an empty dict for now. Callers can pass
   explicit filters to ``HybridSearch.search(query, top_k, filters=...)``
   directly; the ``ProcessedQuery.filters`` field is a hook for
   future query-time parsing (e.g. ``site:wikipedia.com`` style
   faceting). The structure is part of the contract so D2/D5
   don't need to change when query-side parsing lands.

The class is dependency-injectable for tests — pass a
``SparseEncoder`` (or anything with a ``tokenize(text)`` method)
to override the default tokenizer.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.core.types import ProcessedQuery
from src.ingestion.embedding.sparse_encoder import SparseEncoder

if TYPE_CHECKING:
    pass


# Matches simple ``key:value`` filter expressions in a query,
# e.g. ``site:wikipedia`` or ``doc_type:pdf``. We only parse this
# if a future revision of this module wants to; the current
# version returns an empty filters dict regardless of what the
# query contains.
_FILTER_TOKEN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.+)$")


class QueryProcessor:
    """
    Convert a raw query string into a :class:`ProcessedQuery`.

    Args:
        sparse_encoder: Tokenizer used to extract keywords. Should
            be the same one used to build the BM25 index (or a
            compatible one). Defaults to a fresh
            :class:`SparseEncoder`.
        extract_filters: When True, scan the query for ``k:v``
            tokens and populate ``ProcessedQuery.filters``. The
            matched tokens are also stripped from ``keywords`` so
            they don't double-count. Defaults to False (filters
            stay empty; callers pass them to the retriever).
    """

    def __init__(
        self,
        sparse_encoder: SparseEncoder | None = None,
        *,
        extract_filters: bool = False,
    ) -> None:
        self.tokenizer = sparse_encoder or SparseEncoder()
        self.extract_filters = extract_filters

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def process(self, query: str) -> ProcessedQuery:
        """
        Extract keywords (and optionally filters) from ``query``.
        """
        if not query:
            return ProcessedQuery(
                original="", keywords=[], filters={},
            )

        tokens = self._raw_tokens(query)

        keywords: list[str] = []
        filters: dict[str, str] = {}
        if self.extract_filters:
            for tok in tokens:
                m = _FILTER_TOKEN_RE.match(tok)
                if m:
                    filters[m.group(1)] = m.group(2)
                    continue
                # Non-filter tokens get fed through the same
                # tokenizer the indexer uses.
                keywords.extend(self.tokenizer.tokenize(tok))
        else:
            # Flatten: tokenize the whole query in one go so
            # shared tokens across whitespace boundaries still
            # normalize correctly.
            keywords = self.tokenizer.tokenize(query)

        return ProcessedQuery(
            original=query,
            keywords=keywords,
            filters=filters,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _raw_tokens(query: str) -> list[str]:
        """Whitespace-split the query into raw tokens. Used only when
        ``extract_filters=True`` to keep the filter-parsing logic
        separate from the BM25 tokenizer."""
        return [tok for tok in query.split() if tok]


__all__ = ["QueryProcessor"]
