"""Phase 5 T5.1: tests for the local multimodal OCR/caption sub-chunk producer."""

from __future__ import annotations

import pytest

from src.document_parser.vision.multimodal_ingest import (
    _echo_likely,
    cleanup_vision_response,
    produce_vision_subchunks,
    supports_vision_probe,
    vision_supported,
)


class FakeVisionLLM:
    capabilities = {"text", "vision"}
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
    def chat(self, messages, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


class TextOnlyLLM:
    capabilities = {"text"}


def test_vision_supported_routes_on_capability() -> None:
    assert vision_supported(FakeVisionLLM(["x", "y"])) is True
    assert vision_supported(TextOnlyLLM()) is False


def test_cleanup_detects_refusal_and_empty() -> None:
    assert cleanup_vision_response("", "ocr") == ("", False)
    assert cleanup_vision_response("   ", "ocr") == ("", False)
    assert cleanup_vision_response("抱歉，我无法处理图片", "ocr") == ("", False)
    assert cleanup_vision_response("i cannot assist", "ocr") == ("", False)
    ok_text, ok = cleanup_vision_response("| a | b |\n| 1 | 2 |", "ocr")
    assert ok is True and "| a | b |" in ok_text


def test_echo_detection() -> None:
    prompt = "这是一段用于 OCR 的完整提示词说明文字。"
    assert _echo_likely(prompt + "其余", prompt) is True
    assert _echo_likely("这是对图片的真实转写结果，与提示词无关的内容。", prompt) is False


def test_probe_uses_real_image_when_vision() -> None:
    llm = FakeVisionLLM(["ok"])
    assert supports_vision_probe(llm) is True
    assert llm.calls >= 1
    assert supports_vision_probe(TextOnlyLLM()) is False


def test_produce_subchunks_generates_ocr_and_caption() -> None:
    llm = FakeVisionLLM(["| a | b |\n| 1 | 2 |", "图表增长趋势明显"])
    subs = produce_vision_subchunks(
        llm,
        image_bytes=b"\x89PNG-fake",
        mime_type="image/png",
        doc_id="d1",
        chunk_index=3,
        page=2,
        image_id="img-9",
        model_version="Qwen3.8-27B",
    )
    kinds = {s.content_type for s in subs}
    assert kinds == {"image_ocr", "image_caption"}
    ocr = next(s for s in subs if s.content_type == "image_ocr")
    assert ocr.metadata["parent_doc_id"] == "d1"
    assert ocr.metadata["page"] == 2
    assert ocr.metadata["image_id"] == "img-9"
    assert ocr.metadata["model_version"] == "Qwen3.8-27B"


def test_refused_single_image_skips_not_fails() -> None:
    # vision model refuses; caller should get empty sub-chunks, not crash
    llm = FakeVisionLLM(["抱歉，无法", "抱歉，无法"])
    subs = produce_vision_subchunks(
        llm, image_bytes=b"\x89PNG-fake", mime_type="image/png",
        doc_id="d", chunk_index=0, page=1, image_id="i", model_version="m",
    )
    assert subs == []


def test_non_vision_model_skips() -> None:
    subs = produce_vision_subchunks(
        TextOnlyLLM(), image_bytes=b"\x89PNG-fake", mime_type="image/png",
        doc_id="d", chunk_index=0, page=1, image_id="i", model_version="m",
    )
    assert subs == []