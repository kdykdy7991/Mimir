"""
Unit tests for QueryProcessor (D1).

Covers:
- Basic keyword extraction
- Stopword filtering (the tokenizer is shared with SparseEncoder)
- CJK handling (unigram tokenization)
- Empty / whitespace-only input
- filters always a dict (empty by default)
- ``extract_filters=True`` parses ``key:value`` tokens
- Per-call independence
- Returned type is ``ProcessedQuery``
- Round-trip via ``to_dict``
- Order preserved for repeated tokens
- Punctuation / case normalization
- CJK + ASCII mix
"""

from __future__ import annotations

import pytest

from src.core.query_engine import QueryProcessor
from src.core.types import ProcessedQuery
from src.ingestion.embedding.sparse_encoder import SparseEncoder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def processor() -> QueryProcessor:
    return QueryProcessor()


# ---------------------------------------------------------------------------
# Basic output contract
# ---------------------------------------------------------------------------

class TestOutputContract:
    def test_returns_processed_query(self, processor):
        result = processor.process("hello world")
        assert isinstance(result, ProcessedQuery)

    def test_original_preserved(self, processor):
        result = processor.process("Hello World.")
        assert result.original == "Hello World."

    def test_keywords_is_list(self, processor):
        result = processor.process("BM25 retrieval")
        assert isinstance(result.keywords, list)

    def test_filters_is_dict(self, processor):
        """Acceptance: filters is a dict."""
        result = processor.process("BM25 retrieval")
        assert isinstance(result.filters, dict)

    def test_filters_empty_by_default(self, processor):
        result = processor.process("BM25 collection:docs")
        # Caller passes filters separately — keywords path
        # doesn't auto-parse.
        assert result.filters == {}

    def test_processed_query_serializable(self, processor):
        """``to_dict`` round-trip preserves all fields."""
        result = processor.process("BM25 retrieval")
        d = result.to_dict()
        restored = ProcessedQuery.from_dict(d)
        assert restored.original == result.original
        assert restored.keywords == result.keywords
        assert restored.filters == result.filters


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

class TestKeywordExtraction:
    def test_basic_extraction(self, processor):
        result = processor.process("BM25 retrieval formula")
        # All content words survive (none of them are stopwords)
        assert "bm25" in result.keywords
        assert "retrieval" in result.keywords
        assert "formula" in result.keywords

    def test_stopwords_filtered(self, processor):
        result = processor.process("the quick brown fox")
        for sw in ("the", "a", "an"):
            assert sw not in result.keywords
        # Content words kept
        for w in ("quick", "brown", "fox"):
            assert w in result.keywords

    def test_lowercased(self, processor):
        result = processor.process("BM25 RETRIEVAL")
        # Both upper-case forms normalize to lower
        assert "bm25" in result.keywords
        assert "retrieval" in result.keywords
        assert "BM25" not in result.keywords
        assert "RETRIEVAL" not in result.keywords

    def test_punctuation_stripped(self, processor):
        result = processor.process("Hello, world! How are you?")
        # "hello", "world", "how" survive; "are", "you" are stopwords
        assert "hello" in result.keywords
        assert "world" in result.keywords
        assert "how" in result.keywords

    def test_order_preserved(self, processor):
        result = processor.process("alpha beta gamma delta")
        # Order in the input is preserved in the output
        ks = result.keywords
        # alpha/beta/gamma/delta should appear in this order
        assert ks.index("alpha") < ks.index("beta")
        assert ks.index("beta") < ks.index("gamma")
        assert ks.index("gamma") < ks.index("delta")

    def test_repeated_term(self, processor):
        """A repeated term should appear more than once (the
        downstream BM25 retriever uses this)."""
        result = processor.process("BM25 BM25 BM25 retrieval")
        # Three bm25 occurrences
        assert result.keywords.count("bm25") == 3
        assert result.keywords.count("retrieval") == 1

    def test_pure_digits_dropped(self, processor):
        result = processor.process("year 2024 saw 100 events")
        # Default SparseEncoder drops pure-digit tokens
        assert "2024" not in result.keywords
        assert "100" not in result.keywords
        assert "year" in result.keywords
        assert "events" in result.keywords


# ---------------------------------------------------------------------------
# CJK
# ---------------------------------------------------------------------------

class TestCjk:
    def test_cjk_unigrams(self, processor):
        """CJK is unigram-tokenized (matches SparseEncoder)."""
        result = processor.process("BM25 检索 算法")
        assert "bm25" in result.keywords
        # 检索 → 检 + 索
        assert "检" in result.keywords
        assert "索" in result.keywords
        # 算法 → 算 + 法
        assert "算" in result.keywords
        assert "法" in result.keywords

    def test_cjk_stopwords_filtered(self, processor):
        # 的, 了, 是 are in the default stopword list (per
        # SparseEncoder's default stopwords).
        result = processor.process("BM25 的是 算法")
        assert "bm25" in result.keywords
        assert "的" not in result.keywords
        assert "是" not in result.keywords
        # 算法 → 算 + 法
        assert "算" in result.keywords
        assert "法" in result.keywords


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_query(self, processor):
        result = processor.process("")
        assert result.keywords == []
        assert result.filters == {}
        assert result.original == ""

    def test_whitespace_only(self, processor):
        result = processor.process("   \n\t  ")
        assert result.keywords == []

    def test_punctuation_only(self, processor):
        result = processor.process("!!! ??? ...")
        assert result.keywords == []

    def test_long_query(self, processor):
        long = "BM25 " * 100
        result = processor.process(long)
        # Tokenizer is order-preserving; 100 bm25 occurrences expected
        assert result.keywords.count("bm25") == 100

    def test_two_calls_independent(self, processor):
        a = processor.process("BM25 retrieval")
        b = processor.process("different query")
        assert a.keywords != b.keywords


# ---------------------------------------------------------------------------
# extract_filters=True — parses ``k:v`` tokens
# ---------------------------------------------------------------------------

class TestFilterExtraction:
    @pytest.fixture
    def filter_processor(self) -> QueryProcessor:
        return QueryProcessor(extract_filters=True)

    def test_filter_parsed_and_keyword_stripped(self, filter_processor):
        result = filter_processor.process("BM25 site:wikipedia")
        assert result.filters == {"site": "wikipedia"}
        # "site:wikipedia" is a filter token → not a keyword
        assert "site" not in result.keywords
        # "BM25" still a keyword
        assert "bm25" in result.keywords

    def test_multiple_filters(self, filter_processor):
        result = filter_processor.process(
            "BM25 site:wikipedia doc_type:pdf"
        )
        assert result.filters == {
            "site": "wikipedia",
            "doc_type": "pdf",
        }
        assert "bm25" in result.keywords
        # The filter keys themselves aren't keywords
        for k in result.filters:
            assert k not in result.keywords

    def test_value_can_contain_spaces_only_via_split(self, filter_processor):
        """Values are space-delimited (one token). To embed a
        space, the user needs to quote; we keep it simple."""
        result = filter_processor.process("title:hello world")
        assert result.filters == {"title": "hello"}
        assert "world" in result.keywords

    def test_no_filter_when_no_colon(self, filter_processor):
        result = filter_processor.process("BM25 retrieval")
        assert result.filters == {}
        assert "bm25" in result.keywords

    def test_filter_key_must_start_with_letter_or_underscore(
        self, filter_processor
    ):
        """A colon starting the token, or a digit-starting key, is
        not a valid filter — it's treated as a regular keyword."""
        result = filter_processor.process("123:value hello")
        # "123:value" doesn't match the key regex → not a filter
        assert "123" not in result.filters
        # "hello" is a keyword
        assert "hello" in result.keywords


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------

class TestDependencyInjection:
    def test_custom_tokenizer(self):
        """Injecting a custom tokenizer overrides the default."""
        custom = SparseEncoder(stopwords={"bm25", "is"})
        proc = QueryProcessor(sparse_encoder=custom)
        result = proc.process("BM25 is retrieval")
        # "bm25" and "is" are dropped by the custom stopwords
        assert "bm25" not in result.keywords
        assert "is" not in result.keywords
        # "retrieval" still present
        assert "retrieval" in result.keywords

    def test_default_uses_fresh_sparse_encoder(self):
        """Each QueryProcessor instance has its own tokenizer
        (no shared state across instances)."""
        p1 = QueryProcessor()
        p2 = QueryProcessor()
        # Mutating one's tokenizer config shouldn't affect the
        # other.
        p1.tokenizer.stopwords = frozenset()
        assert p2.tokenizer.stopwords != frozenset()
