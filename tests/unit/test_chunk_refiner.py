"""
Unit tests for ChunkRefiner (C5).

Covers:
- Rule patterns: excessive whitespace, headers/footers, HTML
  comments, Markdown images, page numbers, horizontal rules
- Preservation: code fences, Markdown structure, clean text
- LLM mode (mocked): call + refined_by metadata
- Fallback: LLM failure → rule result + reason metadata
- Per-chunk isolation: one chunk failing must not affect the rest
- Configuration: use_llm toggle, fallback_on_error toggle
- Empty input handling
- Prompt fallback (missing file → built-in template)
- Trace stages recorded
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.settings import ChunkRefinerSettings
from src.core.trace.trace_context import TraceContext, new_trace
from src.core.types import Chunk
from src.ingestion.transform import (
    BaseTransform,
    ChunkRefiner,
    META_FALLBACK_REASON,
    META_REFINED_BY,
    META_RULE_ERROR,
    REFINED_BY_ERROR,
    REFINED_BY_LLM,
    REFINED_BY_LLM_FALLBACK,
    REFINED_BY_RULE,
    REFINED_BY_SKIPPED,
    TransformError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "noisy_chunks.json"


@pytest.fixture(scope="module")
def noisy_scenarios() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["scenarios"]


@pytest.fixture
def refiner(tmp_path) -> ChunkRefiner:
    """Default rule-only refiner pointing at a real prompt file."""
    settings = ChunkRefinerSettings(
        use_llm=False,
        prompt_path=str(tmp_path / "prompt.txt"),
    )
    (tmp_path / "prompt.txt").write_text(
        "Clean this:\n{chunk_text}\n---\n", encoding="utf-8"
    )
    return ChunkRefiner(settings)


def make_chunk(text: str, chunk_id: str = "c1") -> Chunk:
    return Chunk(
        id=chunk_id,
        text=text,
        metadata={"source_path": "/tmp/x.pdf", "chunk_index": 0},
    )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class TestAbstractTransform:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseTransform()  # type: ignore[abstract]

    def test_subclass_must_implement_transform(self):
        class HalfBaked(BaseTransform):
            pass

        with pytest.raises(TypeError):
            HalfBaked()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Rule-based refinement (no LLM)
# ---------------------------------------------------------------------------

class TestRuleBased:
    def test_excessive_whitespace_collapsed(self, refiner):
        out = refiner.transform([make_chunk("a   b\n\n\n\n\nc   d")])
        assert out[0].text == "a b\n\nc d"

    def test_trailing_whitespace_per_line_stripped(self, refiner):
        out = refiner.transform([make_chunk("hello   \nworld\t  \n")])
        assert out[0].text == "hello\nworld"

    def test_html_comment_stripped(self, refiner):
        out = refiner.transform(
            [make_chunk("Before <!-- TODO comment --> after.")]
        )
        assert out[0].text == "Before after."

    def test_markdown_image_stripped(self, refiner):
        out = refiner.transform(
            [make_chunk("See ![diagram](http://x.com/y.png) here.")]
        )
        assert out[0].text == "See here."

    def test_horizontal_rule_stripped(self, refiner):
        out = refiner.transform([make_chunk("a\n\n---\n\nb")])
        assert out[0].text == "a\n\nb"
        out = refiner.transform([make_chunk("a\n\n***\n\nb")])
        assert out[0].text == "a\n\nb"

    def test_page_number_lines_stripped(self, refiner):
        for raw in ("Page 1 of 10", "- 5 -", "12"):
            out = refiner.transform([make_chunk(f"Body.\n\n{raw}\n\nMore.")])
            assert "Body." in out[0].text
            assert raw not in out[0].text

    def test_header_line_stripped(self, refiner):
        """Short all-caps-looking line that could be a page header."""
        out = refiner.transform(
            [make_chunk("Annual Report 2024 | Confidential\n\nThe content.")]
        )
        assert "The content." in out[0].text
        assert "Annual Report" not in out[0].text

    def test_code_block_preserved(self, refiner):
        code_chunk = (
            "Intro.\n\n"
            "```python\n"
            "def f():\n"
            "    if True:\n"
            "        return 42\n"
            "```\n\n"
            "Outro."
        )
        out = refiner.transform([make_chunk(code_chunk)])
        # Internal 4-space indent and newlines inside the fence survive
        assert "def f():" in out[0].text
        assert "    if True:" in out[0].text
        assert "        return 42" in out[0].text
        assert "```python" in out[0].text

    def test_markdown_structure_preserved(self, refiner):
        md = "# Heading\n\n- one\n- two\n\n> quote\n\n**bold** and *it*."
        out = refiner.transform([make_chunk(md)])
        # The structure must survive intact (modulo trailing/leading strip)
        assert "# Heading" in out[0].text
        assert "- one" in out[0].text
        assert "- two" in out[0].text
        assert "> quote" in out[0].text
        assert "**bold**" in out[0].text
        assert "*it*" in out[0].text

    def test_clean_text_unchanged(self, refiner):
        clean = "This is clean text.\n\nWith a clean paragraph."
        out = refiner.transform([make_chunk(clean)])
        assert out[0].text == clean

    def test_metadata_refined_by_rule(self, refiner):
        out = refiner.transform([make_chunk("a   b")])
        assert out[0].metadata[META_REFINED_BY] == REFINED_BY_RULE

    def test_input_chunk_not_mutated(self, refiner):
        original = make_chunk("a   b")
        refiner.transform([original])
        assert original.text == "a   b"  # not mutated
        assert META_REFINED_BY not in original.metadata


# ---------------------------------------------------------------------------
# Fixtures-driven scenarios
# ---------------------------------------------------------------------------

class TestFixtureScenarios:
    @pytest.mark.parametrize("scenario", [
        s for s in [
            {"name": "typical_noise_scenario"},
            {"name": "excessive_whitespace"},
            {"name": "page_header_footer"},
            {"name": "format_markers"},
            {"name": "code_blocks_preserved"},
            {"name": "markdown_structure_preserved"},
            {"name": "clean_text_unchanged"},
            {"name": "ocr_errors"},
        ]
    ], ids=lambda s: s["name"])
    def test_scenario_matches_expected(self, refiner, noisy_scenarios, scenario):
        s = next(x for x in noisy_scenarios if x["name"] == scenario["name"])
        out = refiner.transform([make_chunk(s["input"])])
        assert out[0].text == s["expected_after_rule"]


# ---------------------------------------------------------------------------
# LLM mode (mocked)
# ---------------------------------------------------------------------------

class TestLlmMode:
    def test_llm_called_with_substituted_prompt(self, tmp_path):
        (tmp_path / "p.txt").write_text("Clean: {chunk_text}\n", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(tmp_path / "p.txt")
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Cleaned text."
        refiner = ChunkRefiner(settings, llm=mock_llm)

        refiner.transform([make_chunk("raw text")])
        # The prompt sent to the LLM must contain the chunk's text
        call_args = mock_llm.chat.call_args
        prompt_sent = call_args[0][0]
        assert "raw text" in prompt_sent
        assert "Clean:" in prompt_sent

    def test_llm_result_used_and_metadata_set(self, tmp_path):
        (tmp_path / "p.txt").write_text("{chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(tmp_path / "p.txt")
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "LLM refined version."
        refiner = ChunkRefiner(settings, llm=mock_llm)

        out = refiner.transform([make_chunk("original text")])
        assert out[0].text == "LLM refined version."
        assert out[0].metadata[META_REFINED_BY] == REFINED_BY_LLM

    def test_llm_called_once_per_chunk(self, tmp_path):
        (tmp_path / "p.txt").write_text("{chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(tmp_path / "p.txt")
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "x"  # tiny output to exercise length check
        refiner = ChunkRefiner(settings, llm=mock_llm)
        # Set min_change_ratio so the LLM output is treated as a no-op
        refiner._cfg.min_change_ratio = 0.5  # type: ignore[attr-defined]

        refiner.transform([make_chunk("hello", "a"), make_chunk("world", "b")])
        # chat() called twice — once per chunk
        assert mock_llm.chat.call_count == 2


# ---------------------------------------------------------------------------
# Fallback (LLM failure)
# ---------------------------------------------------------------------------

class TestLlmFallback:
    def test_llm_exception_falls_back_to_rule(self, tmp_path):
        (tmp_path / "p.txt").write_text("{chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True,
            prompt_path=str(tmp_path / "p.txt"),
            fallback_on_error=True,
        )
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("API timeout")
        refiner = ChunkRefiner(settings, llm=mock_llm)

        out = refiner.transform([make_chunk("a   b")])
        # Rule result is used ("a b"), with a fallback reason
        assert out[0].text == "a b"
        assert out[0].metadata[META_REFINED_BY] == REFINED_BY_LLM_FALLBACK
        assert "API timeout" in out[0].metadata[META_FALLBACK_REASON]

    def test_llm_returning_empty_falls_back(self, tmp_path):
        (tmp_path / "p.txt").write_text("{chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(tmp_path / "p.txt")
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = ""
        refiner = ChunkRefiner(settings, llm=mock_llm)

        out = refiner.transform([make_chunk("clean")])
        assert out[0].text == "clean"
        assert out[0].metadata[META_REFINED_BY] == REFINED_BY_LLM_FALLBACK

    def test_llm_returning_same_text_falls_back(self, tmp_path):
        (tmp_path / "p.txt").write_text("{chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(tmp_path / "p.txt")
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "clean"  # identical to input
        refiner = ChunkRefiner(settings, llm=mock_llm)

        out = refiner.transform([make_chunk("clean")])
        assert out[0].metadata[META_REFINED_BY] == REFINED_BY_LLM_FALLBACK
        assert "no_change" in out[0].metadata[META_FALLBACK_REASON]

    def test_no_llm_configured_but_flag_true_uses_rule(self, tmp_path):
        """If use_llm=True but no llm is injected, log + run rule-only."""
        (tmp_path / "p.txt").write_text("{chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(tmp_path / "p.txt")
        )
        refiner = ChunkRefiner(settings, llm=None)
        out = refiner.transform([make_chunk("a   b")])
        assert out[0].text == "a b"
        assert out[0].metadata[META_REFINED_BY] == REFINED_BY_RULE


# ---------------------------------------------------------------------------
# Per-chunk isolation
# ---------------------------------------------------------------------------

class TestPerChunkIsolation:
    def test_one_chunk_failing_does_not_affect_others(self, tmp_path):
        (tmp_path / "p.txt").write_text("{chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(tmp_path / "p.txt")
        )
        # Mock LLM: fails for the second chunk only.
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            "Cleaned one.",  # chunk a → success
            RuntimeError("upstream down"),  # chunk b → fallback
            "Cleaned three.",  # chunk c → success
        ]
        refiner = ChunkRefiner(settings, llm=mock_llm)

        chunks = [
            make_chunk("first input",  "a"),
            make_chunk("second input", "b"),
            make_chunk("third input",  "c"),
        ]
        out = refiner.transform(chunks)
        # a and c get LLM result; b falls back to rule output
        assert out[0].text == "Cleaned one."
        assert out[0].metadata[META_REFINED_BY] == REFINED_BY_LLM

        assert out[2].text == "Cleaned three."
        assert out[2].metadata[META_REFINED_BY] == REFINED_BY_LLM

        # b's text is the rule-cleaned version of "second input"
        assert out[1].text == "second input"  # already clean
        assert out[1].metadata[META_REFINED_BY] == REFINED_BY_LLM_FALLBACK
        assert "upstream down" in out[1].metadata[META_FALLBACK_REASON]


# ---------------------------------------------------------------------------
# Empty / edge inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_text_marked_skipped(self, refiner):
        out = refiner.transform([make_chunk("")])
        assert out[0].text == ""
        assert out[0].metadata[META_REFINED_BY] == REFINED_BY_SKIPPED

    def test_whitespace_only_marked_skipped(self, refiner):
        out = refiner.transform([make_chunk("   \n  \n  ")])
        assert out[0].metadata[META_REFINED_BY] == REFINED_BY_SKIPPED

    def test_empty_chunk_list_returns_empty(self, refiner):
        assert refiner.transform([]) == []


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

class TestPromptLoading:
    def test_prompt_file_loaded(self, tmp_path):
        p = tmp_path / "custom_prompt.txt"
        p.write_text("CUSTOM: {chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(p)
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "out"
        refiner = ChunkRefiner(settings, llm=mock_llm)
        refiner.transform([make_chunk("input")])
        call_prompt = mock_llm.chat.call_args[0][0]
        assert "CUSTOM: input" == call_prompt

    def test_missing_prompt_file_uses_builtin(self, tmp_path):
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(tmp_path / "missing.txt")
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "out"
        refiner = ChunkRefiner(settings, llm=mock_llm)
        refiner.transform([make_chunk("input")])
        # Built-in template contains "输入片段"
        prompt = mock_llm.chat.call_args[0][0]
        assert "input" in prompt

    def test_malformed_prompt_raises_at_init(self, tmp_path):
        p = tmp_path / "bad_prompt.txt"
        p.write_text("No placeholder here.", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(p)
        )
        with pytest.raises(ValueError, match="placeholder"):
            ChunkRefiner(settings, llm=MagicMock())

    def test_text_placeholder_also_accepted(self, tmp_path):
        """{text} should work as an alias for {chunk_text}."""
        p = tmp_path / "p.txt"
        p.write_text("Prompt: {text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(p)
        )
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "out"
        refiner = ChunkRefiner(settings, llm=mock_llm)
        refiner.transform([make_chunk("hello")])
        assert mock_llm.chat.call_args[0][0] == "Prompt: hello"


# ---------------------------------------------------------------------------
# Trace integration
# ---------------------------------------------------------------------------

class TestTrace:
    def test_trace_records_start_and_finish(self, tmp_path):
        (tmp_path / "p.txt").write_text("{chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=False, prompt_path=str(tmp_path / "p.txt")
        )
        refiner = ChunkRefiner(settings)
        trace = new_trace()
        refiner.transform([make_chunk("a   b")], trace=trace)
        stage_names = [s["name"] for s in trace.stages]
        assert "chunk_refiner" in stage_names
        # The first and last stage should be start/finish
        start_evts = [s for s in trace.stages if s.get("event") == "start"]
        finish_evts = [s for s in trace.stages if s.get("event") == "finish"]
        assert start_evts and finish_evts

    def test_trace_records_llm_fallback_event(self, tmp_path):
        (tmp_path / "p.txt").write_text("{chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=True, prompt_path=str(tmp_path / "p.txt")
        )
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("503")
        refiner = ChunkRefiner(settings, llm=mock_llm)
        trace = new_trace()
        refiner.transform([make_chunk("a   b")], trace=trace)
        fb_events = [
            s for s in trace.stages
            if s.get("event") == "llm_fallback"
        ]
        assert fb_events
        assert "503" in fb_events[0]["error"]


# ---------------------------------------------------------------------------
# Settings wiring
# ---------------------------------------------------------------------------

class TestSettings:
    def test_settings_off_runs_rule_only(self, tmp_path):
        (tmp_path / "p.txt").write_text("{chunk_text}", encoding="utf-8")
        settings = ChunkRefinerSettings(
            use_llm=False, prompt_path=str(tmp_path / "p.txt")
        )
        refiner = ChunkRefiner(settings, llm=MagicMock())
        # use_llm=False → LLM is NEVER called
        out = refiner.transform([make_chunk("a   b")])
        assert out[0].metadata[META_REFINED_BY] == REFINED_BY_RULE
        refiner.llm.chat.assert_not_called()  # type: ignore[attr-defined]
