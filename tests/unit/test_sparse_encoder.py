"""
Unit tests for SparseEncoder (C9).

Verifies:
- Output structure: list[ChunkRecord] with sparse_vector = {term: weight}
- Order preserved
- Tokenization: ASCII words, CJK unigrams, stopwords, digits, length filter
- BM25 math: idf, tf saturation, length normalization
- Common vs rare terms: rare terms get higher weight
- Empty / whitespace input → empty dict (never None)
- C9 contract: output is usable by BM25Indexer (downstream C11)
- Configuration: k1, b, lowercase, stopwords, min_term_len
- Trace integration
"""

from __future__ import annotations

import math

import pytest

from src.core.trace.trace_context import new_trace
from src.core.types import Chunk, ChunkRecord
from src.ingestion.embedding import SparseEncoder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def encoder() -> SparseEncoder:
    return SparseEncoder()


def make_chunk(text: str, chunk_id: str = "c1") -> Chunk:
    return Chunk(
        id=chunk_id,
        text=text,
        metadata={"source_path": "/tmp/x.pdf", "chunk_index": 0},
    )


# ---------------------------------------------------------------------------
# Basic output contract
# ---------------------------------------------------------------------------

class TestOutputContract:
    def test_empty_input_returns_empty(self, encoder):
        assert encoder.encode([]) == []

    def test_output_is_list_of_chunk_records(self, encoder):
        out = encoder.encode([make_chunk("hello world", "a")])
        assert isinstance(out, list)
        assert all(isinstance(r, ChunkRecord) for r in out)

    def test_sparse_vector_is_dict_not_none(self, encoder):
        out = encoder.encode([make_chunk("some real text here", "a")])
        assert isinstance(out[0].sparse_vector, dict)
        assert out[0].sparse_vector is not None

    def test_dense_vector_left_none(self, encoder):
        """SparseEncoder only fills sparse_vector; dense stays None."""
        out = encoder.encode([make_chunk("hello world", "a")])
        assert out[0].dense_vector is None

    def test_count_matches_input(self, encoder):
        chunks = [make_chunk(f"text {i}", f"c{i}") for i in range(5)]
        out = encoder.encode(chunks)
        assert len(out) == 5

    def test_order_preserved(self, encoder):
        chunks = [
            make_chunk("alpha beta", "a"),
            make_chunk("gamma delta", "b"),
            make_chunk("epsilon zeta", "c"),
        ]
        out = encoder.encode(chunks)
        assert [r.id for r in out] == ["a", "b", "c"]
        assert "alpha" in out[0].sparse_vector
        assert "gamma" in out[1].sparse_vector
        assert "epsilon" in out[2].sparse_vector


# ---------------------------------------------------------------------------
# Empty / edge inputs (spec requirement: "对空文本有明确行为")
# ---------------------------------------------------------------------------

class TestEmptyText:
    def test_empty_string_produces_empty_dict(self, encoder):
        out = encoder.encode([make_chunk("", "a")])
        assert out[0].sparse_vector == {}

    def test_whitespace_only_produces_empty_dict(self, encoder):
        out = encoder.encode([make_chunk("   \n  \t  ", "a")])
        assert out[0].sparse_vector == {}

    def test_punctuation_only_produces_empty_dict(self, encoder):
        out = encoder.encode([make_chunk("!!! ??? ...", "a")])
        assert out[0].sparse_vector == {}

    def test_all_empty_chunks(self, encoder):
        chunks = [make_chunk("", str(i)) for i in range(3)]
        out = encoder.encode(chunks)
        assert all(r.sparse_vector == {} for r in out)

    def test_mix_empty_and_nonempty(self, encoder):
        out = encoder.encode([
            make_chunk("hello world", "a"),
            make_chunk("", "b"),
            make_chunk("foo bar", "c"),
        ])
        assert "hello" in out[0].sparse_vector
        assert out[1].sparse_vector == {}
        assert "foo" in out[2].sparse_vector


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

class TestTokenization:
    def test_ascii_words_lowercased(self, encoder):
        out = encoder.encode([make_chunk("Hello WORLD hello", "a")])
        # "hello" and "world" both present, lowercased
        assert "hello" in out[0].sparse_vector
        assert "world" in out[0].sparse_vector

    def test_punctuation_stripped(self, encoder):
        out = encoder.encode([make_chunk("hello, world! foo.", "a")])
        assert "hello" in out[0].sparse_vector
        assert "world" in out[0].sparse_vector
        assert "foo" in out[0].sparse_vector
        # No punctuation in keys
        for k in out[0].sparse_vector:
            assert "," not in k and "!" not in k and "." not in k

    def test_stopwords_filtered(self, encoder):
        out = encoder.encode([make_chunk(
            "the quick brown fox is not lazy", "a"
        )])
        # All common English stopwords gone
        for sw in ("the", "is", "not", "a", "an"):
            assert sw not in out[0].sparse_vector
        # Content words kept
        for w in ("quick", "brown", "fox", "lazy"):
            assert w in out[0].sparse_vector

    def test_cjk_tokenized_as_unigrams(self, encoder):
        # Use CJK characters that are NOT in the default stopword list
        # ("你" = "you" is a stopword, so we can't use real phrases
        # like "你好世界").
        out = encoder.encode([make_chunk("编程语言", "a")])
        # Each CJK char is a separate unigram
        for c in ("编", "程", "语", "言"):
            assert c in out[0].sparse_vector

    def test_cjk_mixed_with_ascii(self, encoder):
        out = encoder.encode([make_chunk(
            "BM25 检索 引擎 排序 算法", "a"
        )])
        assert "bm25" in out[0].sparse_vector
        # CJK is unigram-tokenized: each char is its own token, so the
        # multi-char words are split into individual characters.
        for ch in ("检", "索", "引", "擎", "排", "序", "算", "法"):
            assert ch in out[0].sparse_vector, f"missing {ch}"

    def test_pure_digits_dropped_by_default(self, encoder):
        out = encoder.encode([make_chunk("year 2024 saw 100 events", "a")])
        assert "2024" not in out[0].sparse_vector
        assert "100" not in out[0].sparse_vector
        assert "year" in out[0].sparse_vector
        assert "events" in out[0].sparse_vector

    def test_pure_digits_kept_when_disabled(self):
        enc = SparseEncoder(drop_pure_digits=False)
        out = enc.encode([make_chunk("year 2024", "a")])
        assert "2024" in out[0].sparse_vector

    def test_lowercase_disabled_preserves_case(self):
        enc = SparseEncoder(lowercase=False)
        out = enc.encode([make_chunk("Hello HELLO", "a")])
        # Both cases are different keys now
        assert "Hello" in out[0].sparse_vector
        assert "HELLO" in out[0].sparse_vector
        assert "hello" not in out[0].sparse_vector

    def test_min_term_len_filters_short(self):
        enc = SparseEncoder(min_term_len=3)
        out = enc.encode([make_chunk("a an the foo bar", "a")])
        # "a" and "an" are too short, "the" is stopword, "foo" and "bar" pass
        assert "foo" in out[0].sparse_vector
        assert "bar" in out[0].sparse_vector
        assert "a" not in out[0].sparse_vector
        assert "an" not in out[0].sparse_vector

    def test_custom_stopwords(self):
        enc = SparseEncoder(stopwords=["foo", "bar"])
        out = enc.encode([make_chunk("foo bar baz", "a")])
        assert "foo" not in out[0].sparse_vector
        assert "bar" not in out[0].sparse_vector
        assert "baz" in out[0].sparse_vector

    def test_empty_stopwords_disables_filter(self):
        enc = SparseEncoder(stopwords=[])
        out = enc.encode([make_chunk("the a an", "a")])
        # No stopword filtering — all words survive
        assert "the" in out[0].sparse_vector


# ---------------------------------------------------------------------------
# BM25 weight math
# ---------------------------------------------------------------------------

class TestBm25Math:
    def test_repeated_term_in_chunk_higher_weight_than_single(self, encoder):
        """Within a single chunk, a term repeated 3x should have a
        higher weight than a term appearing only once (all else equal)."""
        out = encoder.encode([
            make_chunk("alpha alpha alpha", "a"),
            make_chunk("alpha", "b"),
        ])
        # alpha's weight in 'a' (3 occurrences) > alpha's weight in 'b' (1)
        w_repeated = out[0].sparse_vector["alpha"]
        w_single = out[1].sparse_vector["alpha"]
        assert w_repeated > w_single

    def test_rare_term_higher_weight_than_common(self, encoder):
        """In a 3-doc corpus, a term in 1 doc is rarer → higher idf →
        higher weight (per occurrence) than a term in all 3 docs."""
        out = encoder.encode([
            make_chunk("rare common", "a"),
            make_chunk("common other", "b"),
            make_chunk("common another", "c"),
        ])
        w_rare = out[0].sparse_vector["rare"]
        w_common = out[0].sparse_vector["common"]
        assert w_rare > w_common

    def test_term_in_all_docs_low_but_positive(self, encoder):
        """A term appearing in every doc has df=N → idf is small but
        the BM25+1 form keeps it non-negative."""
        out = encoder.encode([
            make_chunk("foo alpha", "a"),
            make_chunk("foo beta", "b"),
            make_chunk("foo gamma", "c"),
        ])
        # "foo" is in all 3 docs; its weight should be > 0
        w = out[0].sparse_vector["foo"]
        assert w > 0

    def test_weights_are_non_negative(self, encoder):
        out = encoder.encode([
            make_chunk("alpha beta gamma", "a"),
            make_chunk("delta epsilon", "b"),
        ])
        for r in out:
            for term, w in r.sparse_vector.items():
                assert w >= 0, f"negative weight for {term}: {w}"

    def test_higher_k1_increases_repetition_benefit(self):
        """A higher k1 should give a bigger boost to a term that
        appears many times vs few."""
        enc_low = SparseEncoder(k1=0.5)
        enc_high = SparseEncoder(k1=3.0)
        out_low = enc_low.encode([
            make_chunk("alpha alpha alpha alpha", "a")
        ])
        out_high = enc_high.encode([
            make_chunk("alpha alpha alpha alpha", "a")
        ])
        # In a single-doc corpus, idf is 0 → weight should be ~0
        # regardless. Use a multi-doc corpus to make idf > 0.
        out_low = enc_low.encode([
            make_chunk("alpha alpha alpha alpha", "a"),
            make_chunk("alpha", "b"),
        ])
        out_high = enc_high.encode([
            make_chunk("alpha alpha alpha alpha", "a"),
            make_chunk("alpha", "b"),
        ])
        # Difference between repeated and single occurrence widens with k1
        diff_low = out_low[0].sparse_vector["alpha"] - out_low[1].sparse_vector["alpha"]
        diff_high = out_high[0].sparse_vector["alpha"] - out_high[1].sparse_vector["alpha"]
        assert diff_high > diff_low

    def test_length_normalization_short_doc_gets_higher_tf(self):
        """With b>0, a short doc with the same tf as a long doc should
        have a higher normalized term frequency (and thus higher weight)."""
        enc = SparseEncoder(k1=1.5, b=0.75)
        # Two docs with the same single occurrence of "alpha" but very
        # different lengths → short doc wins on tf_norm.
        out = enc.encode([
            make_chunk("alpha", "short"),                # dl=1
            make_chunk("alpha " + "z " * 50, "long"),     # dl=2
        ])
        w_short = out[0].sparse_vector["alpha"]
        w_long = out[1].sparse_vector["alpha"]
        assert w_short > w_long

    def test_idf_matches_known_formula(self, encoder):
        """For df=1 in N=3 docs, idf = log((3-1+0.5)/(1+0.5) + 1) = log(2)."""
        # Construct corpus: 3 docs, "unique" appears in doc 0 only.
        out = encoder.encode([
            make_chunk("unique alpha", "a"),
            make_chunk("beta", "b"),
            make_chunk("gamma", "c"),
        ])
        # "unique" tf=1, dl=2, avgdl = (2+1+1)/3 = 4/3
        # idf = log((3-1+0.5)/(1+0.5) + 1) = log(2.5/1.5 + 1) = log(8/3)
        expected_idf = math.log((3 - 1 + 0.5) / (1 + 0.5) + 1.0)
        # tf_norm = (1*2.5) / (1 + 1.5*(1-0.75 + 0.75*2/(4/3)))
        avgdl = (2 + 1 + 1) / 3
        denom = 1 + 1.5 * (1 - 0.75 + 0.75 * 2 / avgdl)
        expected_tf_norm = (1 * 2.5) / denom
        expected_w = expected_idf * expected_tf_norm
        assert abs(out[0].sparse_vector["unique"] - expected_w) < 1e-9


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------

class TestConfig:
    def test_k1_negative_raises(self):
        with pytest.raises(ValueError, match="k1"):
            SparseEncoder(k1=-1)

    def test_b_out_of_range_raises(self):
        with pytest.raises(ValueError, match="b"):
            SparseEncoder(b=1.5)
        with pytest.raises(ValueError, match="b"):
            SparseEncoder(b=-0.1)

    def test_min_term_len_too_small_raises(self):
        with pytest.raises(ValueError, match="min_term_len"):
            SparseEncoder(min_term_len=0)


# ---------------------------------------------------------------------------
# Field preservation
# ---------------------------------------------------------------------------

class TestFieldPreservation:
    def test_other_chunk_fields_preserved(self, encoder):
        chunk = Chunk(
            id="myid",
            text="hello world",
            metadata={"source_path": "/tmp/x.pdf", "chunk_index": 3},
            start_offset=10,
            end_offset=20,
            source_ref="doc1",
        )
        out = encoder.encode([chunk])
        rec = out[0]
        assert rec.id == "myid"
        assert rec.text == "hello world"
        assert rec.metadata == {"source_path": "/tmp/x.pdf", "chunk_index": 3}
        assert rec.start_offset == 10
        assert rec.end_offset == 20
        assert rec.source_ref == "doc1"

    def test_input_chunk_not_mutated(self, encoder):
        chunk = make_chunk("hello world", "a")
        encoder.encode([chunk])
        assert isinstance(chunk, Chunk)  # not upgraded to ChunkRecord


# ---------------------------------------------------------------------------
# Downstream contract
# ---------------------------------------------------------------------------

class TestDownstreamContract:
    """The spec requires the output to be 'usable by bm25_indexer'."""

    def test_every_record_has_sparse_vector_field(self, encoder):
        out = encoder.encode([
            make_chunk("alpha", "a"),
            make_chunk("", "b"),
            make_chunk("beta gamma", "c"),
        ])
        for r in out:
            assert hasattr(r, "sparse_vector")
            assert isinstance(r.sparse_vector, dict)

    def test_sparse_vector_roundtrips_through_dataclass(self, encoder):
        out = encoder.encode([make_chunk("hello world", "a")])
        d = out[0].to_dict()
        assert "sparse_vector" in d
        assert isinstance(d["sparse_vector"], dict)
        # Reconstructing from the dict yields an equivalent record
        restored = ChunkRecord.from_dict(d)
        assert restored.sparse_vector == out[0].sparse_vector


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

class TestTrace:
    def test_trace_records_start_and_finish(self, encoder):
        trace = new_trace()
        encoder.encode([make_chunk("a b"), make_chunk("c d")], trace=trace)
        events = [s.get("event") for s in trace.stages
                  if s.get("name") == "sparse_encoder"]
        assert "start" in events
        assert "finish" in events

    def test_trace_records_unique_term_count(self, encoder):
        trace = new_trace()
        encoder.encode([
            make_chunk("alpha beta", "a"),
            make_chunk("alpha gamma", "b"),
        ], trace=trace)
        finish = next(s for s in trace.stages
                      if s.get("event") == "finish")
        assert finish["n_unique_terms"] == 3  # alpha, beta, gamma



# ---------------------------------------------------------------------------
# CJK 1+2-gram char n-gram tokenization
# ---------------------------------------------------------------------------

class TestCjkNgram:
    """The default tokenizer emits 1+2-gram character n-grams for
    CJK runs. This is the BM25 signal the sparse leg relies on:
    the 1-gram keeps coverage broad, the 2-gram is what gives
    common short Chinese terms ("考勤", "加班") their real
    tokenization instead of dropping back to single characters."""

    def test_one_gram(self, encoder):
        # Every individual CJK char must be present.
        tokens = encoder.tokenize("员工请假流程")
        for ch in "员工请假流程":
            assert ch in tokens

    def test_two_gram(self, encoder):
        # Every adjacent CJK pair must be present.
        for pair in ["员工", "工请", "请假", "假流", "流程"]:
            assert pair in encoder.tokenize("员工请假流程")

    def test_one_grams_before_two_grams(self, encoder):
        """Emission order: all 1-grams first, then all 2-grams. This
        keeps the 1-gram stream contiguous (so a downstream reader
        sees the same word-by-word walk the unigram tokenizer would)
        and groups the 2-grams at the tail (so bigrams don't
        interleave with the unigram stream)."""
        tokens = encoder.tokenize("员工请假流程")
        # First 6 are the 1-grams.
        assert tokens[:6] == ["员", "工", "请", "假", "流", "程"]
        # Then the 2-grams follow.
        assert tokens[6:] == ["员工", "工请", "请假", "假流", "流程"]

    def test_output_is_deterministic(self, encoder):
        # Char n-gram output is order-dependent but stable. Two calls
        # on the same input must produce identical token lists.
        a = encoder.tokenize("员工请假流程")
        b = encoder.tokenize("员工请假流程")
        assert a == b

    def test_single_cjk_char_produces_no_bigram(self, encoder):
        # A lone CJK char has no neighbour → just the 1-gram.
        assert encoder.tokenize("考") == ["考"]

    def test_ascii_run_is_not_split_into_ngrams(self, encoder):
        # ASCII must keep its whole-word behaviour, never split into
        # adjacent bigrams.
        assert encoder.tokenize("BM25") == ["bm25"]
        assert encoder.tokenize("hello") == ["hello"]

    def test_ascii_breaks_cjk_run(self, encoder):
        """An ASCII run in the middle of a CJK string must terminate
        the current CJK run — no bigram may cross the CJK/ASCII
        boundary."""
        tokens = encoder.tokenize("员工abc请假")
        # "员工" is a valid 2-gram (CJK chars 0-1).
        assert "员工" in tokens
        # "工请" is NOT — they have "abc" between them.
        assert "工请" not in tokens
        # "请假" IS valid (CJK chars 5-6 after the ASCII gap).
        assert "请假" in tokens

    def test_punctuation_breaks_cjk_run(self, encoder):
        # "，" is not CJK, not ASCII alnum, so the run ends there.
        tokens = encoder.tokenize("考勤,管理")
        # 1-gram of each is preserved.
        for ch in "考勤管理":
            assert ch in tokens
        # But the comma blocks the 勤管 bigram.
        assert "勤管" not in tokens

    def test_mixed_text_drops_punctuation(self, encoder):
        # Punctuation has no 1-gram and no bigram.
        tokens = encoder.tokenize("考勤，管理！")
        for ch in "，。！":
            assert ch not in tokens
        # But the real CJK content survives.
        for ch in "考勤管理":
            assert ch in tokens

    def test_empty_and_whitespace(self, encoder):
        assert encoder.tokenize("") == []
        assert encoder.tokenize("   \n\t ") == []

    def test_filters_apply_to_bigrams_too(self, encoder):
        # stopwords / min_term_len / drop_pure_digits should all
        # affect bigrams identically to 1-grams.
        enc = SparseEncoder(
            stopwords=["考勤"], min_term_len=2, drop_pure_digits=True,
        )
        tokens = enc.tokenize("2024 年考勤管理")
        # stopword filters 考勤 even when it appears as a bigram.
        assert "考勤" not in tokens
        # 2-gram "管理" survives because "管理" is not a stopword.
        assert "管理" in tokens
        # Pure digit tokens still dropped.
        assert "2024" not in tokens
        # min_term_len=2 drops 1-grams but keeps 2-grams.
        assert "考" not in tokens
        assert "勤" not in tokens
        assert "管" not in tokens
        assert "理" not in tokens

    def test_lowercase_does_not_affect_cjk(self, encoder):
        # CJK has no case. Only ASCII tokens should be lowercased.
        tokens = encoder.tokenize("BM25 考勤")
        assert "bm25" in tokens
        for ch in "考勤":
            assert ch in tokens

    def test_encode_produces_weights_for_bigrams(self, encoder):
        """End-to-end: 2-grams flow through to the chunk's
        sparse_vector and get weighted, not just emitted as tokens."""
        out = encoder.encode([make_chunk("员工请假流程", "a")])
        v = out[0].sparse_vector
        # The 2-gram must have a non-zero weight.
        assert "员工" in v
        assert v["员工"] > 0
        # The 1-gram must also be weighted.
        assert "员" in v
        assert v["员"] > 0


# ---------------------------------------------------------------------------
# from_settings factory
# ---------------------------------------------------------------------------

class TestFromSettings:
    def test_none_settings_returns_defaults(self):
        enc = SparseEncoder.from_settings(None)
        assert enc.min_term_len == 1
        assert enc.drop_pure_digits is True

    def test_no_arg_returns_defaults(self):
        enc = SparseEncoder.from_settings()
        assert enc.drop_pure_digits is True

    def test_maps_every_settings_field(self):
        """Guard against a field being added to SparseSettings but not
        wired through the factory — the bug class that made ingestion
        and query tokenize differently."""
        from src.core.settings import SparseSettings

        settings = SparseSettings(
            stopwords=["foo", "bar"],
            min_term_len=3,
            drop_pure_digits=False,
        )
        enc = SparseEncoder.from_settings(settings)
        assert "foo" in enc.stopwords
        assert "bar" in enc.stopwords
        assert enc.min_term_len == 3
        assert enc.drop_pure_digits is False

    def test_default_settings_object_matches_bare_constructor(self):
        from src.core.settings import SparseSettings

        from_settings = SparseEncoder.from_settings(SparseSettings())
        bare = SparseEncoder()
        assert from_settings.min_term_len == bare.min_term_len
        assert from_settings.drop_pure_digits == bare.drop_pure_digits
        assert from_settings.stopwords == bare.stopwords

    def test_stopwords_none_uses_builtin_defaults(self):
        from src.core.settings import SparseSettings

        enc = SparseEncoder.from_settings(
            SparseSettings(stopwords=None),
        )
        # Built-in English stopwords are still active.
        assert "the" in enc.stopwords

    def test_tokenizer_field_no_longer_exists(self):
        """Locking the removal: the only tokenizer we support is
        1+2-gram. If a future config file still passes ``tokenizer:
        jieba``, the field must not silently re-introduce jieba."""
        from src.core.settings import SparseSettings

        # Pydantic v2 ignores unknown fields by default; this confirms
        # the field isn't even declared.
        assert "tokenizer" not in SparseSettings.model_fields

    def test_extras_are_ignored_not_crashed(self):
        """If a stale config still passes ``tokenizer: jieba``, we
        must not crash — we just ignore the unknown field."""
        from src.core.settings import SparseSettings

        settings = SparseSettings(tokenizer="jieba")  # type: ignore[call-arg]
        enc = SparseEncoder.from_settings(settings)
        # Still produces 1+2-gram tokens.
        assert "员工" in enc.tokenize("员工请假")
