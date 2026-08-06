"""
Contract tests for MetadataEnricher (C6).

Covers:
- Abstract interface contract
- Rule mode: title / summary / tags always non-empty for any non-empty input
- Rule mode: tags are content words (no stopwords), lowercased, deduped
- LLM mode (mocked): valid JSON → result used + enriched_by="llm"
- LLM mode: invalid JSON → fallback to rule + reason
- LLM mode: exception → fallback to rule + reason
- Markdown code-fence stripping around JSON
- Per-chunk isolation
- Settings toggle respected
- Title / summary length truncation
- Empty / whitespace-only input handled
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.core.settings import MetadataEnricherSettings
from src.core.trace.trace_context import new_trace
from src.core.types import Chunk
from src.ingestion.transform import (
    BaseTransform,
    MetadataEnricher,
    META_ENRICH_FALLBACK,
    META_ENRICHED_BY,
    META_SUMMARY,
    META_TAGS,
    META_TITLE,
    ENRICHED_BY_ERROR,
    ENRICHED_BY_LLM,
    ENRICHED_BY_LLM_FALLBACK,
    ENRICHED_BY_RULE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def enricher(tmp_path) -> MetadataEnricher:
    """Default rule-only enricher with a real prompt file."""
    p = tmp_path / "prompt.txt"
    p.write_text(
        "Generate metadata for:\n{chunk_text}\n---\n",
        encoding="utf-8",
    )
    return MetadataEnricher(
        MetadataEnricherSettings(
            use_llm=False, prompt_path=str(p),
        )
    )


def make_chunk(text: str, chunk_id: str = "c1") -> Chunk:
    return Chunk(
        id=chunk_id,
        text=text,
        metadata={"source_path": "/tmp/x.pdf", "chunk_index": 0},
    )


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class TestAbstractTransform:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseTransform()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Rule-based enrichment
# ---------------------------------------------------------------------------

class TestRuleBased:
    def test_required_fields_present(self, enricher):
        out = enricher.transform([make_chunk("Some real text content here.")])
        meta = out[0].metadata
        assert META_TITLE in meta
        assert META_SUMMARY in meta
        assert META_TAGS in meta
        assert meta[META_TITLE]  # non-empty
        assert meta[META_SUMMARY]  # non-empty
        assert isinstance(meta[META_TAGS], list)

    def test_title_from_first_line(self, enricher):
        out = enricher.transform([make_chunk(
            "First Line Title\n\nMore text below."
        )])
        assert out[0].metadata[META_TITLE] == "First Line Title"

    def test_title_truncated_to_max_len(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        long_title = "X" * 200
        e = MetadataEnricher(
            MetadataEnricherSettings(
                use_llm=False, prompt_path=str(p), max_title_len=20,
            )
        )
        out = e.transform([make_chunk(long_title)])
        assert len(out[0].metadata[META_TITLE]) <= 20
        assert out[0].metadata[META_TITLE].endswith("…")

    def test_summary_uses_first_sentence(self, enricher):
        out = enricher.transform([make_chunk(
            "First sentence. Second sentence. Third sentence."
        )])
        # Rule keeps first 1-2 sentences
        s = out[0].metadata[META_SUMMARY]
        assert "First sentence" in s
        assert "Second sentence" in s

    def test_summary_truncated_to_max_len(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        e = MetadataEnricher(
            MetadataEnricherSettings(
                use_llm=False, prompt_path=str(p), max_summary_len=30,
            )
        )
        long_text = "Word " * 50
        out = e.transform([make_chunk(long_text)])
        assert len(out[0].metadata[META_SUMMARY]) <= 30

    def test_tags_exclude_stopwords(self, enricher):
        out = enricher.transform([make_chunk(
            "The quick brown fox jumps over the lazy dog. "
            "The fox is very quick and the dog is lazy."
        )])
        tags = out[0].metadata[META_TAGS]
        # All stopwords should be excluded
        for sw in ("the", "a", "an", "is", "and", "over"):
            assert sw not in tags
        # Content words should appear
        assert "fox" in tags
        assert "dog" in tags
        assert "quick" in tags

    def test_tags_lowercased_and_deduped(self, enricher):
        out = enricher.transform([make_chunk(
            "Fox fox FOX Fox dog dog Dog"
        )])
        tags = out[0].metadata[META_TAGS]
        # No duplicates
        assert len(tags) == len(set(tags))
        # All lowercased
        for t in tags:
            assert t == t.lower()

    def test_tags_respect_max_count(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        e = MetadataEnricher(
            MetadataEnricherSettings(
                use_llm=False, prompt_path=str(p), max_tags=3,
            )
        )
        text = "alpha beta gamma delta epsilon zeta eta theta"
        out = e.transform([make_chunk(text)])
        assert len(out[0].metadata[META_TAGS]) <= 3

    def test_metadata_enriched_by_rule(self, enricher):
        out = enricher.transform([make_chunk("some text")])
        assert out[0].metadata[META_ENRICHED_BY] == ENRICHED_BY_RULE

    def test_input_chunk_not_mutated(self, enricher):
        original = make_chunk("some text")
        enricher.transform([original])
        assert META_TITLE not in original.metadata
        assert META_TAGS not in original.metadata

    def test_short_text_still_produces_all_fields(self, enricher):
        out = enricher.transform([make_chunk("hi")])
        meta = out[0].metadata
        assert meta[META_TITLE]  # non-empty
        assert meta[META_SUMMARY]  # non-empty
        # "hi" is 2 chars, not a stopword, so it survives.
        assert isinstance(meta[META_TAGS], list)

    def test_chinese_text_handled(self, enricher):
        out = enricher.transform([make_chunk(
            "BM25 是一种经典的信息检索排序算法。"
            "它基于词频和逆文档频率来计算相关性得分。"
            "BM25 在搜索引擎中被广泛使用。"
        )])
        # Title from first line / sentence
        assert "BM25" in out[0].metadata[META_TITLE]
        # Tags should include BM25 (CJK tokenizer keeps the term)
        assert "bm25" in out[0].metadata[META_TAGS]


# ---------------------------------------------------------------------------
# Empty / edge inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_text_handled(self, enricher):
        out = enricher.transform([make_chunk("")])
        meta = out[0].metadata
        assert meta[META_TITLE]  # non-empty (placeholder)
        assert meta[META_SUMMARY]
        assert meta[META_TAGS] == []
        assert meta[META_ENRICHED_BY] == ENRICHED_BY_RULE

    def test_whitespace_only_handled(self, enricher):
        out = enricher.transform([make_chunk("   \n  \n  ")])
        assert out[0].metadata[META_TITLE]  # placeholder, not empty
        assert out[0].metadata[META_TAGS] == []

    def test_empty_list_returns_empty(self, enricher):
        assert enricher.transform([]) == []


# ---------------------------------------------------------------------------
# LLM mode (mocked)
# ---------------------------------------------------------------------------

class TestLlmMode:
    def test_llm_valid_json_used(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(
            use_llm=True, prompt_path=str(p)
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({
            "title": "LLM Title",
            "summary": "LLM summary here.",
            "tags": ["foo", "bar"],
        })
        e = MetadataEnricher(settings, llm=mock_llm)
        out = e.transform([make_chunk("any text")])
        assert out[0].metadata[META_TITLE] == "LLM Title"
        assert out[0].metadata[META_SUMMARY] == "LLM summary here."
        assert out[0].metadata[META_TAGS] == ["foo", "bar"]
        assert out[0].metadata[META_ENRICHED_BY] == ENRICHED_BY_LLM

    def test_llm_strips_markdown_fence(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(use_llm=True, prompt_path=str(p))
        mock_llm = MagicMock()
        # LLM wraps JSON in a ```json ... ``` fence
        mock_llm.chat.return_value = (
            "```json\n"
            + json.dumps({
                "title": "T", "summary": "S", "tags": ["a"]
            })
            + "\n```"
        )
        e = MetadataEnricher(settings, llm=mock_llm)
        out = e.transform([make_chunk("x")])
        assert out[0].metadata[META_TITLE] == "T"
        assert out[0].metadata[META_ENRICHED_BY] == ENRICHED_BY_LLM

    def test_llm_prompt_substitution(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("META-FOR: {chunk_text}\n--END--", encoding="utf-8")
        settings = MetadataEnricherSettings(use_llm=True, prompt_path=str(p))
        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({
            "title": "t", "summary": "s", "tags": ["k"]
        })
        e = MetadataEnricher(settings, llm=mock_llm)
        e.transform([make_chunk("INPUT_TEXT")])
        sent_prompt = mock_llm.chat.call_args[0][0]
        assert "META-FOR: INPUT_TEXT" in sent_prompt
        assert "--END--" in sent_prompt

    def test_llm_truncates_overlong_title(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(
            use_llm=True, prompt_path=str(p), max_title_len=10
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({
            "title": "X" * 100,
            "summary": "fine",
            "tags": ["a"],
        })
        e = MetadataEnricher(settings, llm=mock_llm)
        out = e.transform([make_chunk("x")])
        assert len(out[0].metadata[META_TITLE]) <= 10


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------

class TestLlmFallback:
    def test_invalid_json_falls_back(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(use_llm=True, prompt_path=str(p))
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "this is not JSON at all"
        e = MetadataEnricher(settings, llm=mock_llm)
        out = e.transform([make_chunk("hello world")])
        # Falls back to rule output
        assert out[0].metadata[META_ENRICHED_BY] == ENRICHED_BY_LLM_FALLBACK
        # Rule still populated the fields
        assert out[0].metadata[META_TITLE]  # non-empty
        # Fallback reason recorded
        assert META_ENRICH_FALLBACK in out[0].metadata
        assert "invalid_llm_output" in out[0].metadata[META_ENRICH_FALLBACK]

    def test_partial_json_falls_back(self, tmp_path):
        """JSON present but missing required fields → fallback."""
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(use_llm=True, prompt_path=str(p))
        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({"title": "only title"})
        e = MetadataEnricher(settings, llm=mock_llm)
        out = e.transform([make_chunk("hello")])
        assert out[0].metadata[META_ENRICHED_BY] == ENRICHED_BY_LLM_FALLBACK

    def test_empty_llm_response_falls_back(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(use_llm=True, prompt_path=str(p))
        mock_llm = MagicMock()
        mock_llm.chat.return_value = ""
        e = MetadataEnricher(settings, llm=mock_llm)
        out = e.transform([make_chunk("hello")])
        assert out[0].metadata[META_ENRICHED_BY] == ENRICHED_BY_LLM_FALLBACK

    def test_llm_exception_falls_back(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(use_llm=True, prompt_path=str(p))
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("503 backend down")
        e = MetadataEnricher(settings, llm=mock_llm)
        out = e.transform([make_chunk("hello world")])
        assert out[0].metadata[META_ENRICHED_BY] == ENRICHED_BY_LLM_FALLBACK
        assert "503" in out[0].metadata[META_ENRICH_FALLBACK]

    def test_use_llm_true_without_llm_runs_rule(self, tmp_path):
        """If use_llm=True but no llm injected, log + run rule-only."""
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(use_llm=True, prompt_path=str(p))
        e = MetadataEnricher(settings, llm=None)
        out = e.transform([make_chunk("hello")])
        assert out[0].metadata[META_ENRICHED_BY] == ENRICHED_BY_RULE
        assert out[0].metadata[META_TITLE]  # rule still worked


# ---------------------------------------------------------------------------
# Per-chunk isolation
# ---------------------------------------------------------------------------

class TestPerChunkIsolation:
    def test_one_chunk_failing_does_not_affect_others(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(use_llm=True, prompt_path=str(p))
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            json.dumps({"title": "OK", "summary": "ok", "tags": ["x"]}),
            RuntimeError("chunk 2 fails"),
            json.dumps({"title": "OK2", "summary": "ok2", "tags": ["y"]}),
        ]
        e = MetadataEnricher(settings, llm=mock_llm)
        chunks = [
            make_chunk("first text",  "a"),
            make_chunk("second text", "b"),
            make_chunk("third text",  "c"),
        ]
        out = e.transform(chunks)
        assert out[0].metadata[META_ENRICHED_BY] == ENRICHED_BY_LLM
        assert out[2].metadata[META_ENRICHED_BY] == ENRICHED_BY_LLM
        # Middle one fell back to rule (still has title/summary/tags)
        assert out[1].metadata[META_ENRICHED_BY] == ENRICHED_BY_LLM_FALLBACK
        assert out[1].metadata[META_TITLE]
        assert "chunk 2 fails" in out[1].metadata[META_ENRICH_FALLBACK]


# ---------------------------------------------------------------------------
# Settings wiring
# ---------------------------------------------------------------------------

class TestSettings:
    def test_use_llm_false_never_calls_llm(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(
            use_llm=False, prompt_path=str(p)
        )
        e = MetadataEnricher(settings, llm=MagicMock())
        e.transform([make_chunk("hello")])
        e.llm.chat.assert_not_called()  # type: ignore[attr-defined]

    def test_prompt_missing_uses_builtin(self, tmp_path):
        settings = MetadataEnricherSettings(
            use_llm=True, prompt_path=str(tmp_path / "missing.txt")
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({
            "title": "t", "summary": "s", "tags": ["k"]
        })
        e = MetadataEnricher(settings, llm=mock_llm)
        e.transform([make_chunk("hi")])
        # Built-in prompt was sent (has Chinese text)
        sent = mock_llm.chat.call_args[0][0]
        assert "hi" in sent

    def test_malformed_prompt_raises_at_init(self, tmp_path):
        p = tmp_path / "bad.txt"
        p.write_text("no placeholder", encoding="utf-8")
        settings = MetadataEnricherSettings(
            use_llm=True, prompt_path=str(p)
        )
        with pytest.raises(ValueError, match="placeholder"):
            MetadataEnricher(settings, llm=MagicMock())


# ---------------------------------------------------------------------------
# Trace integration
# ---------------------------------------------------------------------------

class TestTrace:
    def test_trace_records_start_and_finish(self, enricher):
        trace = new_trace()
        enricher.transform([make_chunk("hello")], trace=trace)
        events = [s.get("event") for s in trace.stages if s.get("name") == "metadata_enricher"]
        assert "start" in events
        assert "finish" in events

    def test_trace_records_llm_fallback(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("{chunk_text}", encoding="utf-8")
        settings = MetadataEnricherSettings(use_llm=True, prompt_path=str(p))
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("503")
        e = MetadataEnricher(settings, llm=mock_llm)
        trace = new_trace()
        e.transform([make_chunk("x")], trace=trace)
        fb = [
            s for s in trace.stages
            if s.get("event") == "llm_fallback"
        ]
        assert fb
        assert "503" in fb[0]["error"]


# ---------------------------------------------------------------------------
# Type contract
# ---------------------------------------------------------------------------

class TestTypeContract:
    def test_output_is_chunk_dataclass(self, enricher):
        out = enricher.transform([make_chunk("some content here")])
        c = out[0]
        # Round-trip through to_dict preserves the new fields
        d = c.to_dict()
        assert META_TITLE in d["metadata"]
        assert META_SUMMARY in d["metadata"]
        assert META_TAGS in d["metadata"]
        assert META_ENRICHED_BY in d["metadata"]
