"""Phase 5 T5.2: tests for the vision-ingest transform (trigger + failure
semantics + sub-chunk metadata wiring)."""

from __future__ import annotations

import pytest

from src.core.types import Chunk
from src.document_parser.vision.multimodal_ingest import VisionSubChunk
from src.ingestion.transform.vision_ingest_transform import (
    VisionIngestTransform,
    should_run_vision,
)


def _chunk(text="body text that is long enough for chunking body 12345", **meta) -> Chunk:
    m = {"chunk_index": 0, "images": [{
        "id": "img-1", "path": "/tmp/img1.png", "mime_type": "image/png",
        "is_content": True,
    }]}
    m.update(meta)
    return Chunk(id="c1", text=text, metadata=m, source_ref="doc-1")


class FakeProducer:
    def __init__(self, result):
        self.result = result
        self.calls = 0
    def __call__(self, llm, **kw):
        self.calls += 1
        return self.result


def _vid(**kw) -> VisionSubChunk:
    ct = kw.pop("content_type", "image_ocr")
    m = {"image_id": kw.pop("image_id", "img-1"), "model_version": kw.pop("model_version", "m")}
    m.update(kw)
    return VisionSubChunk(content_type=ct, text="ocr text", metadata=m)


def test_should_run_vision_triggers() -> None:
    c = _chunk()
    assert should_run_vision(c, has_content_image=True, force_vision=True) is True
    assert should_run_vision(_chunk(image_source_type="scanned_pdf"), has_content_image=True, force_vision=False) is True
    assert should_run_vision(_chunk("short"), has_content_image=True, force_vision=False) is True
    assert should_run_vision(_chunk(text="long enough", has_content_image=True), has_content_image=True, force_vision=False, min_text_chars=30) is True  # short
    ok = _chunk("x" * 200)
    assert should_run_vision(ok, has_content_image=True, force_vision=False, min_text_chars=100) is False


def test_disabled_transform_is_passthrough() -> None:
    c = _chunk()
    out = VisionIngestTransform(enabled=False).transform([c])
    assert out == [c] and "vision_subchunks" not in out[0].metadata


def test_content_image_gets_subchunks_metadata() -> None:
    prod = FakeProducer([_vid(content_type="image_ocr"), _vid(content_type="image_caption")])
    t = VisionIngestTransform(enabled=True, producer=prod, load_bytes=lambda p: b"IMG")
    # scanned chunk triggers the vision path
    out = t.transform([_chunk(image_source_type="scanned_pdf")])
    subs = out[0].metadata["vision_subchunks"]
    assert {s["content_type"] for s in subs} == {"image_ocr", "image_caption"}
    assert prod.calls == 1
    # no false success marker
    assert not out[0].metadata.get("has_unprocessed_images")


def test_decorative_image_skipped() -> None:
    c = _chunk()
    c.metadata["images"] = [{"id": "img-9", "path": "x", "is_content": False}]
    prod = FakeProducer([_vid()])
    t = VisionIngestTransform(enabled=True, producer=prod, load_bytes=lambda p: b"IMG")
    out = t.transform([c])
    assert prod.calls == 0
    assert "vision_subchunks" not in out[0].metadata


def test_scanned_all_failed_raises() -> None:
    c = _chunk(image_source_type="scanned_pdf")
    prod = FakeProducer([])  # every image yields nothing
    t = VisionIngestTransform(enabled=True, producer=prod, load_bytes=lambda p: b"IMG")
    with pytest.raises(RuntimeError):
        t.transform([c])


def test_scanned_partial_failure_ok() -> None:
    c = _chunk(image_source_type="scanned_pdf")
    prod = FakeProducer([_vid()])
    t = VisionIngestTransform(enabled=True, producer=prod, load_bytes=lambda p: b"IMG")
    out = t.transform([c])
    assert "vision_subchunks" in out[0].metadata


def test_digital_partial_failure_is_not_error() -> None:
    c = _chunk("hi")  # short digital text body → triggers vision, not scanned
    prod = FakeProducer([])
    t = VisionIngestTransform(
        enabled=True, producer=prod, load_bytes=lambda p: b"IMG",
        scanned_all_failed_is_error=False,
    )
    out = t.transform([c])
    assert out[0].metadata.get("has_unprocessed_images") is True


def test_subchunks_emitted_as_indexable_chunks() -> None:
    prod = FakeProducer([
        _vid(content_type="image_ocr"), _vid(content_type="image_caption"),
    ])
    t = VisionIngestTransform(enabled=True, producer=prod, load_bytes=lambda p: b"IMG")
    out = t.transform([_chunk(image_source_type="scanned_pdf")])
    # parent + 2 sub-chunks flow through embedding/BM25/vector as Chunks
    assert len(out) == 3
    kinds = {c.metadata["chunk_type"] for c in out if c.metadata.get("is_vision_subchunk")}
    assert kinds == {"image_ocr", "image_caption"}
    sc = next(c for c in out if c.metadata.get("chunk_type") == "image_ocr")
    assert sc.text == "ocr text"
    assert sc.metadata["parent_chunk_id"] == "c1"
    assert sc.metadata["model_version"] == "m"
    assert sc.source_ref == "doc-1"