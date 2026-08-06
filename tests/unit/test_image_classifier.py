"""
Unit tests for ImageContentClassifier (C8).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from src.core.settings import (
    ImageClassifierHardFilterSettings,
    ImageClassifierLLMSettings,
    ImageClassifierSettings,
)
from src.core.types import Chunk, ImageRef
from src.ingestion.transform.image_classifier import ImageContentClassifier


# ---------------------------------------------------------------------------
# Fake LLM for testing
# ---------------------------------------------------------------------------

@dataclass
class FakeLLM:
    """A fake LLM that returns a configurable response."""

    response: str = '{"classification": "content", "reason": "looks useful"}'
    capabilities: set[str] | None = None
    raise_on_call: bool = False

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = {"text", "vision"}

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        if self.raise_on_call:
            raise RuntimeError("simulated LLM failure")
        return self.response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunk(
    images: list[dict[str, Any]],
    text: str = "Some context",
) -> Chunk:
    refs = [img["id"] for img in images if img.get("id")]
    return Chunk(
        id="chunk_001",
        text=text,
        metadata={
            "images": images,
            "image_refs": refs,
        },
    )


def _make_image(
    image_id: str,
    *,
    is_content: bool = True,
    reason: str | None = None,
    path: str = "/data/images/test.png",
) -> dict[str, Any]:
    return {
        "id": image_id,
        "path": path,
        "is_content": is_content,
        "classification_reason": reason,
    }


# ---------------------------------------------------------------------------
# Disabled mode
# ---------------------------------------------------------------------------

class TestDisabledMode:
    def test_disabled_classifier_passes_through_unchanged(self):
        cfg = ImageClassifierSettings(enabled=False)
        classifier = ImageContentClassifier(cfg)
        chunk = _make_chunk([
            _make_image("img1", is_content=True),
            _make_image("img2", is_content=False, reason="size_filter"),
        ])
        out = classifier.transform([chunk])
        assert out[0].metadata["image_refs"] == ["img1", "img2"]
        assert len(out[0].metadata["images"]) == 2


# ---------------------------------------------------------------------------
# Hard-filter passthrough
# ---------------------------------------------------------------------------

class TestHardFilterPassthrough:
    def test_decorative_images_already_marked_are_removed(self):
        cfg = ImageClassifierSettings(enabled=True)
        classifier = ImageContentClassifier(cfg)
        chunk = _make_chunk([
            _make_image("logo", is_content=False, reason="size_filter"),
            _make_image("diagram", is_content=True),
        ])
        out = classifier.transform([chunk])
        assert out[0].metadata["image_refs"] == ["diagram"]
        assert len(out[0].metadata["images"]) == 1
        assert out[0].metadata["images"][0]["id"] == "diagram"

    def test_all_content_images_preserved(self):
        cfg = ImageClassifierSettings(enabled=True)
        classifier = ImageContentClassifier(cfg)
        chunk = _make_chunk([
            _make_image("img1", is_content=True),
            _make_image("img2", is_content=True),
        ])
        out = classifier.transform([chunk])
        assert out[0].metadata["image_refs"] == ["img1", "img2"]


# ---------------------------------------------------------------------------
# LLM judgment
# ---------------------------------------------------------------------------

class TestLLMJudgment:
    def test_llm_marks_decorative(self, tmp_path):
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"fake png bytes")
        cfg = ImageClassifierSettings(
            enabled=True,
            llm=ImageClassifierLLMSettings(enabled=True),
        )
        llm = FakeLLM('{"classification": "decorative", "reason": "just a logo"}')
        classifier = ImageContentClassifier(cfg, llm=llm)
        chunk = _make_chunk([
            _make_image("img1", is_content=True, path=str(img_path)),
        ])
        out = classifier.transform([chunk])
        assert "image_refs" not in out[0].metadata
        assert "images" not in out[0].metadata

    def test_llm_keeps_content(self, tmp_path):
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"fake png bytes")
        cfg = ImageClassifierSettings(
            enabled=True,
            llm=ImageClassifierLLMSettings(enabled=True),
        )
        llm = FakeLLM('{"classification": "content", "reason": "diagram"}')
        classifier = ImageContentClassifier(cfg, llm=llm)
        chunk = _make_chunk([
            _make_image("img1", is_content=True, path=str(img_path)),
        ])
        out = classifier.transform([chunk])
        assert out[0].metadata["image_refs"] == ["img1"]

    def test_keyword_fallback_decorative(self, tmp_path):
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"fake png bytes")
        cfg = ImageClassifierSettings(
            enabled=True,
            llm=ImageClassifierLLMSettings(enabled=True),
        )
        llm = FakeLLM("This looks decorative to me")
        classifier = ImageContentClassifier(cfg, llm=llm)
        chunk = _make_chunk([
            _make_image("img1", is_content=True, path=str(img_path)),
        ])
        out = classifier.transform([chunk])
        assert "image_refs" not in out[0].metadata

    def test_keyword_fallback_content(self, tmp_path):
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"fake png bytes")
        cfg = ImageClassifierSettings(
            enabled=True,
            llm=ImageClassifierLLMSettings(enabled=True),
        )
        llm = FakeLLM("I cannot tell")
        classifier = ImageContentClassifier(cfg, llm=llm)
        chunk = _make_chunk([
            _make_image("img1", is_content=True, path=str(img_path)),
        ])
        out = classifier.transform([chunk])
        assert out[0].metadata["image_refs"] == ["img1"]

    def test_non_vision_model_skips_llm_judgment(self, tmp_path):
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"fake png bytes")
        cfg = ImageClassifierSettings(
            enabled=True,
            llm=ImageClassifierLLMSettings(enabled=True),
        )
        llm = FakeLLM(
            '{"classification": "decorative"}',
            capabilities={"text"},  # no vision
        )
        classifier = ImageContentClassifier(cfg, llm=llm)
        chunk = _make_chunk([
            _make_image("img1", is_content=True, path=str(img_path)),
        ])
        out = classifier.transform([chunk])
        # Without vision capability, image is kept as content.
        assert out[0].metadata["image_refs"] == ["img1"]

    def test_llm_error_with_fallback_keeps_content(self, tmp_path):
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"fake png bytes")
        cfg = ImageClassifierSettings(
            enabled=True,
            llm=ImageClassifierLLMSettings(enabled=True, fallback_on_error=True),
        )
        llm = FakeLLM(raise_on_call=True)
        classifier = ImageContentClassifier(cfg, llm=llm)
        chunk = _make_chunk([
            _make_image("img1", is_content=True, path=str(img_path)),
        ])
        out = classifier.transform([chunk])
        assert out[0].metadata["image_refs"] == ["img1"]

    def test_missing_image_file_keeps_content(self, tmp_path):
        cfg = ImageClassifierSettings(
            enabled=True,
            llm=ImageClassifierLLMSettings(enabled=True),
        )
        llm = FakeLLM('{"classification": "decorative"}')
        classifier = ImageContentClassifier(cfg, llm=llm)
        chunk = _make_chunk([
            _make_image("img1", is_content=True, path=str(tmp_path / "missing.png")),
        ])
        out = classifier.transform([chunk])
        assert out[0].metadata["image_refs"] == ["img1"]


# ---------------------------------------------------------------------------
# No images
# ---------------------------------------------------------------------------

class TestNoImages:
    def test_chunk_without_images_passes_through(self):
        cfg = ImageClassifierSettings(enabled=True)
        classifier = ImageContentClassifier(cfg)
        chunk = Chunk(id="c1", text="no images here", metadata={})
        out = classifier.transform([chunk])
        assert out[0] is chunk
