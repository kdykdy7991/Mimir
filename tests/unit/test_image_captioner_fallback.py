"""
Unit tests for ImageCaptioner (C7).

Covers:
- Enabled mode (mock Vision LLM): captions generated + written
  into chunk.metadata["image_captions"]
- Disabled mode: chunks with image_refs get
  ``has_unprocessed_images = True``, NO LLM call
- LLM unavailable: same as disabled
- LLM exception: per-image fallback, not chunk-aborting
- LLM returns empty/None: image marked unprocessed
- No images on chunk: no metadata change, no LLM call
- Per-chunk isolation: one bad image doesn't block others
- Caption truncation
- Image path lookup from chunk.metadata["images"]
- Per-chunk metadata isolation (no input mutation)
- Trace integration
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.settings import ImageCaptionerSettings
from src.core.trace.trace_context import new_trace
from src.core.types import Chunk
from src.ingestion.transform import (
    ImageCaptioner,
    META_CAPTIONS,
    META_UNPROCESSED,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_chunk(
    text: str = "The chart shows BM25 performance.",
    image_refs: list[str] | None = None,
    images: list[dict] | None = None,
    chunk_id: str = "c1",
) -> Chunk:
    meta: dict = {"source_path": "/tmp/x.pdf", "chunk_index": 0}
    if image_refs is not None:
        meta["image_refs"] = image_refs
    if images is not None:
        meta["images"] = images
    return Chunk(id=chunk_id, text=text, metadata=meta)


@pytest.fixture
def captioner(tmp_path) -> ImageCaptioner:
    p = tmp_path / "caption_prompt.txt"
    p.write_text(
        "Describe the image at {image_path} given context:\n{context}\n",
        encoding="utf-8",
    )
    return ImageCaptioner(
        ImageCaptionerSettings(
            use_llm=False,
            prompt_path=str(p),
        )
    )


# ---------------------------------------------------------------------------
# Disabled / unavailable
# ---------------------------------------------------------------------------

class TestDisabledMode:
    def test_disabled_no_llm_call(self, captioner):
        chunk = make_chunk(image_refs=["img1"])
        out = captioner.transform([chunk])
        # No LLM was injected → no captions produced, but the
        # chunk is still in the output and flagged as unprocessed.
        assert out[0].metadata[META_UNPROCESSED] is True
        assert META_CAPTIONS not in out[0].metadata

    def test_disabled_preserves_image_refs(self, captioner):
        chunk = make_chunk(image_refs=["img1", "img2"])
        out = captioner.transform([chunk])
        assert out[0].metadata["image_refs"] == ["img1", "img2"]

    def test_use_llm_true_but_no_llm_object(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=True, prompt_path=str(p)),
            llm=None,
        )
        chunk = make_chunk(image_refs=["img1"])
        out = c.transform([chunk])
        # No LLM injected → degraded mode
        assert out[0].metadata[META_UNPROCESSED] is True

    def test_no_image_refs_means_no_change(self, captioner):
        chunk = make_chunk()  # no image_refs
        out = captioner.transform([chunk])
        # No marker added, no captions
        assert META_UNPROCESSED not in out[0].metadata
        assert META_CAPTIONS not in out[0].metadata
        assert out[0].metadata == chunk.metadata


# ---------------------------------------------------------------------------
# Enabled mode (mock Vision LLM)
# ---------------------------------------------------------------------------

class TestEnabledMode:
    def test_caption_written_to_metadata(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=True, prompt_path=str(p)),
            llm=MagicMock(chat=MagicMock(return_value="A line chart.")),
        )
        chunk = make_chunk(image_refs=["img1"])
        out = c.transform([chunk])
        assert META_CAPTIONS in out[0].metadata
        captions = out[0].metadata[META_CAPTIONS]
        assert len(captions) == 1
        assert captions[0] == {"id": "img1", "caption": "A line chart."}
        # No unprocessed flag — all images captioned
        assert META_UNPROCESSED not in out[0].metadata

    def test_llm_called_once_per_image(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = ["caption 1", "caption 2", "caption 3"]
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=True, prompt_path=str(p)),
            llm=mock_llm,
        )
        chunk = make_chunk(image_refs=["a", "b", "c"])
        out = c.transform([chunk])
        assert mock_llm.chat.call_count == 3
        captions = out[0].metadata[META_CAPTIONS]
        assert [c2["id"] for c2 in captions] == ["a", "b", "c"]
        assert [c2["caption"] for c2 in captions] == [
            "caption 1", "caption 2", "caption 3"
        ]

    def test_prompt_substitution(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text(
            "Image at {image_path}; Context: {context}; Body: {text}",
            encoding="utf-8",
        )
        mock_llm = MagicMock(chat=MagicMock(return_value="ok"))
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=True, prompt_path=str(p)),
            llm=mock_llm,
        )
        chunk = make_chunk(
            text="the surrounding paragraph",
            image_refs=["img1"],
            images=[{
                "id": "img1",
                "path": "/data/images/doc/img1.png",
            }],
        )
        c.transform([chunk])
        sent = mock_llm.chat.call_args[0][0]
        assert "/data/images/doc/img1.png" in sent
        assert "the surrounding paragraph" in sent
        # The {text} alias is also substituted
        assert sent.count("the surrounding paragraph") == 2

    def test_image_path_optional(self, tmp_path):
        """If chunk.metadata['images'] is missing, the LLM is still
        called — just without a path hint."""
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        mock_llm = MagicMock(chat=MagicMock(return_value="cap"))
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=True, prompt_path=str(p)),
            llm=mock_llm,
        )
        chunk = make_chunk(
            image_refs=["img1"],
            images=None,  # no image records
        )
        out = c.transform([chunk])
        assert len(out[0].metadata[META_CAPTIONS]) == 1

    def test_caption_truncated_to_max(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        long_caption = "X" * 1000
        c = ImageCaptioner(
            ImageCaptionerSettings(
                use_llm=True, prompt_path=str(p), max_caption_len=50,
            ),
            llm=MagicMock(chat=MagicMock(return_value=long_caption)),
        )
        chunk = make_chunk(image_refs=["img1"])
        out = c.transform([chunk])
        cap = out[0].metadata[META_CAPTIONS][0]["caption"]
        assert len(cap) <= 50
        assert cap.endswith("…")


# ---------------------------------------------------------------------------
# Fallback (LLM error / empty result)
# ---------------------------------------------------------------------------

class TestFallback:
    def test_llm_exception_marks_unprocessed(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("upstream down")
        c = ImageCaptioner(
            ImageCaptionerSettings(
                use_llm=True, prompt_path=str(p),
                fallback_on_error=True,
            ),
            llm=mock_llm,
        )
        chunk = make_chunk(image_refs=["img1"])
        out = c.transform([chunk])
        assert out[0].metadata[META_UNPROCESSED] is True
        assert META_CAPTIONS not in out[0].metadata
        # image_refs still preserved
        assert out[0].metadata["image_refs"] == ["img1"]

    def test_llm_returning_empty_marks_unprocessed(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=True, prompt_path=str(p)),
            llm=MagicMock(chat=MagicMock(return_value="")),
        )
        chunk = make_chunk(image_refs=["img1"])
        out = c.transform([chunk])
        assert out[0].metadata[META_UNPROCESSED] is True
        assert META_CAPTIONS not in out[0].metadata

    def test_fallback_off_propagates_exception(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("upstream down")
        c = ImageCaptioner(
            ImageCaptionerSettings(
                use_llm=True, prompt_path=str(p),
                fallback_on_error=False,
            ),
            llm=mock_llm,
        )
        chunk = make_chunk(image_refs=["img1"])
        with pytest.raises(RuntimeError, match="upstream down"):
            c.transform([chunk])

    def test_per_image_isolation_on_failure(self, tmp_path):
        """If the 2nd of 3 image calls fails, images 1 and 3 are
        still captioned and only img2 ends up unprocessed."""
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            "caption 1",
            RuntimeError("flaky"),
            "caption 3",
        ]
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=True, prompt_path=str(p)),
            llm=mock_llm,
        )
        chunk = make_chunk(image_refs=["a", "b", "c"])
        out = c.transform([chunk])
        captions = out[0].metadata[META_CAPTIONS]
        # The 2nd call failed, so captions for "a" and "c" are
        # written, but "b" is in the unprocessed set (which is
        # implicit — unprocessed = all image_refs not in captions).
        assert {c2["id"] for c2 in captions} == {"a", "c"}
        assert out[0].metadata[META_UNPROCESSED] is True


# ---------------------------------------------------------------------------
# Per-chunk isolation
# ---------------------------------------------------------------------------

class TestPerChunkIsolation:
    def test_one_chunk_failing_does_not_affect_others(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        mock_llm = MagicMock()
        # chunk 0 (no images) → no LLM call
        # chunk 1 (with images) → success
        # chunk 2 (with images) → exception
        # chunk 3 (with images) → success
        mock_llm.chat.side_effect = [
            "caption for chunk 1",
            RuntimeError("bad"),
            "caption for chunk 3",
        ]
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=True, prompt_path=str(p)),
            llm=mock_llm,
        )
        chunks = [
            make_chunk(text="no images here", chunk_id="a"),
            make_chunk(text="first with image", image_refs=["x1"], chunk_id="b"),
            make_chunk(text="second with image", image_refs=["x2"], chunk_id="c"),
            make_chunk(text="third with image", image_refs=["x3"], chunk_id="d"),
        ]
        out = c.transform(chunks)

        # a: no images → no metadata change
        assert META_UNPROCESSED not in out[0].metadata
        assert META_CAPTIONS not in out[0].metadata

        # b: success
        assert out[1].metadata[META_CAPTIONS][0]["id"] == "x1"
        assert META_UNPROCESSED not in out[1].metadata

        # c: failed → unprocessed
        assert out[2].metadata[META_UNPROCESSED] is True
        assert META_CAPTIONS not in out[2].metadata

        # d: success
        assert out[3].metadata[META_CAPTIONS][0]["id"] == "x3"

    def test_input_chunk_not_mutated(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=True, prompt_path=str(p)),
            llm=MagicMock(chat=MagicMock(return_value="cap")),
        )
        chunk = make_chunk(image_refs=["img1"])
        original_meta = dict(chunk.metadata)
        c.transform([chunk])
        # Input metadata is unchanged
        assert chunk.metadata == original_meta


# ---------------------------------------------------------------------------
# Trace integration
# ---------------------------------------------------------------------------

class TestTrace:
    def test_disabled_mode_records_skipped_event(self, captioner):
        trace = new_trace()
        chunk = make_chunk(image_refs=["img1"])
        captioner.transform([chunk], trace=trace)
        events = [
            s.get("event") for s in trace.stages
            if s.get("name") == "image_captioner"
        ]
        assert "start" in events
        assert "skipped" in events
        assert "finish" in events

    def test_enabled_records_captioned_event(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("CTX: {context}", encoding="utf-8")
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=True, prompt_path=str(p)),
            llm=MagicMock(chat=MagicMock(return_value="cap")),
        )
        trace = new_trace()
        c.transform([make_chunk(image_refs=["img1"])], trace=trace)
        events = [
            s for s in trace.stages
            if s.get("name") == "image_captioner"
            and s.get("event") == "captioned"
        ]
        assert events
        assert events[0]["n_captioned"] == 1
        assert events[0]["n_unprocessed"] == 0


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

class TestPromptLoading:
    def test_prompt_file_loaded(self, tmp_path):
        p = tmp_path / "p.txt"
        p.write_text("PROMPT: {context}", encoding="utf-8")
        c = ImageCaptioner(
            ImageCaptionerSettings(use_llm=False, prompt_path=str(p))
        )
        assert "PROMPT:" in c._prompt_template  # type: ignore[attr-defined]

    def test_missing_prompt_uses_builtin(self, tmp_path):
        c = ImageCaptioner(
            ImageCaptionerSettings(
                use_llm=False, prompt_path=str(tmp_path / "nope.txt")
            )
        )
        assert "{context}" in c._prompt_template  # type: ignore[attr-defined]
