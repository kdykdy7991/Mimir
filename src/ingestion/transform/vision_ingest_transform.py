"""
Vision ingest transform (Phase 5) — multimodal OCR / Caption sub-chunks.

Trigger conditions (plan §Phase-5): image is content; the page is `scanned_pdf`;
the chunk text is empty / too short (low quality); an image table is present;
or the user forced `force_vision=true`.

For each qualifying content image this transform runs the T5.1 producer
(``produce_vision_subchunks``) to get ``image_ocr`` / ``image_caption`` sub-chunk
descriptors and attaches them to the chunk as ``vision_subchunks`` metadata.

Failure semantics (plan §Phase-5):
* a single image failure is a warning — other images keep going;
* a scanned document where *every* page's visual parse failed must fail the
  document task, NOT be marked as success with empty placeholders;
* a digital document with a failing decorative/image failure may keep its body
  (``partial_success``).

The transform never fabricates success: empty / refused output yields no
sub-chunk and increments the failure counter.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from src.core.types import Chunk
from src.document_parser.vision.multimodal_ingest import (
    VisionSubChunk,
    produce_vision_subchunks,
)
from src.ingestion.transform.base_transform import BaseTransform

logger = logging.getLogger(__name__)

META_SUBCHUNKS = "vision_subchunks"
META_UNPROCESSED = "has_unprocessed_images"
META_OCR = "image_ocr"
META_CAPTION = "image_caption"

DEFAULT_MIN_TEXT_CHARS = 30


def should_run_vision(
    chunk: Chunk,
    *,
    has_content_image: bool,
    force_vision: bool,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
) -> bool:
    """Apply the Phase-5 trigger conditions."""
    if force_vision:
        return True
    if not has_content_image:
        return False
    meta = chunk.metadata or {}
    if meta.get("image_source_type") == "scanned_pdf":
        return True
    if meta.get("image_table"):
        return True
    body = (chunk.text or "").strip()
    if not body or len(body) < min_text_chars:
        return True
    return False


def _image_records(chunk: Chunk) -> list[dict[str, Any]]:
    raw = chunk.metadata.get("images") or []
    records = []
    for img in raw:
        if not isinstance(img, dict):
            continue
        # decorative images should not be OCRed as content
        if not img.get("is_content", True):
            continue
        records.append(img)
    return records


class VisionIngestTransform(BaseTransform):
    """
    Attach ``image_ocr`` / ``image_caption`` sub-chunks to content images.

    Args:
        enabled: master switch (off by default; feature flagged).
        producer: callable matching ``produce_vision_subchunks``; injectable
            for tests. Defaults to the real T5.1 producer.
        min_text_chars: low-quality text threshold for the trigger.
        scanned_all_failed_is_error: when True, a scanned doc with total failure
            raises (task failure) instead of silently succeeding.
        load_bytes: callable(image_path) -> bytes, to read an image on disk.
    """

    name = "vision_ingest"

    def __init__(
        self,
        *,
        enabled: bool = False,
        llm=None,
        producer: Callable[..., list[VisionSubChunk]] | None = None,
        min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
        scanned_all_failed_is_error: bool = True,
        load_bytes: Callable[[str], bytes] | None = None,
    ) -> None:
        self.enabled = enabled
        self.llm = llm
        self._producer = producer or produce_vision_subchunks
        self._min_text_chars = min_text_chars
        self._scanned_all_failed_is_error = scanned_all_failed_is_error
        self._load_bytes = load_bytes or self._default_load_bytes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def transform(self, chunks: list[Chunk], trace=None) -> list[Chunk]:
        if not self.enabled:
            return chunks  # feature off — pipeline unchanged
        out: list[Chunk] = []
        all_failed = True
        any_attempted = False
        for chunk in chunks:
            processed, attempted, failed = self._process_chunk(chunk)
            out.append(processed)
            if attempted:
                any_attempted = True
                if not failed:
                    all_failed = False
        if (
            self._scanned_all_failed_is_error
            and any_attempted
            and all_failed
            and self._is_scanned(chunks)
        ):
            raise RuntimeError(
                "scanned document: all page visual parses failed; refusing to "
                "mark empty placeholders as success",
            )
        return out

    def _process_chunk(self, chunk: Chunk) -> tuple[Chunk, bool, bool]:
        records = _image_records(chunk)
        if not records:
            return chunk, False, False
        forced = bool((chunk.metadata or {}).get("force_vision"))
        if not should_run_vision(
            chunk, has_content_image=bool(records), force_vision=forced,
            min_text_chars=self._min_text_chars,
        ):
            return chunk, False, False

        produced: list[VisionSubChunk] = []
        failed = 0
        for img in records:
            try:
                raw = self._load_bytes(str(img.get("path") or ""))
            except Exception as exc:  # noqa: BLE001
                logger.warning("vision: cannot read image %s: %s", img.get("id"), exc)
                failed += 1
                continue
            try:
                subs = self._produce(
                    raw,
                    mime=str(img.get("mime_type") or "image/png"),
                    doc_id=str(chunk.source_ref or ""),
                    chunk_index=int(chunk.metadata.get("chunk_index", 0)),
                    page=int(chunk.metadata.get("page") or chunk.metadata.get("page_number") or 0),
                    image_id=str(img.get("id") or ""),
                    model_version=str(img.get("model_version") or ""),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("vision produce failed for %s: %s", img.get("id"), exc)
                failed += 1
                continue
            if not subs:
                failed += 1
            produced.extend(subs)

        meta = copy.deepcopy(chunk.metadata)
        if produced:
            meta[META_SUBCHUNKS] = [s.to_chunk_dict() for s in produced]
        if failed and produced:
            meta[META_UNPROCESSED] = True
        if failed and not produced:
            meta[META_UNPROCESSED] = True
            # never set success marker for an all-failed attempt
        return replace(chunk, metadata=meta), True, (not produced)

    def _produce(self, raw: bytes, **kw: Any) -> list[VisionSubChunk]:
        """Run the producer (injectable for tests) against the current LLM."""
        return self._producer(self.llm, image_bytes=raw, **kw)

    @staticmethod
    def _is_scanned(chunks: list[Chunk]) -> bool:
        return any((c.metadata or {}).get("image_source_type") == "scanned_pdf" for c in chunks)

    @staticmethod
    def _default_load_bytes(path: str) -> bytes:
        return Path(path).read_bytes()