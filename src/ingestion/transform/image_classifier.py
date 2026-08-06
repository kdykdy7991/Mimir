"""
ImageContentClassifier (C8) — optional Vision LLM classification for image refs.

Runs after chunking and before ``ImageCaptioner``. It reuses the injected
LLM to judge whether an image carries informational value (content) or is
decorative (logo, icon, border, repeated header/footer image).

Two sources of classification:

1. **Hard filters** (already applied by ``PdfLoader``) mark obvious
   decorative images by setting ``ImageRef.is_content = False``. This
   transform respects those marks and skips LLM calls for them.
2. **LLM judgment** (optional) asks a vision-capable model to classify
   images that passed the hard filters. This catches cases the rules miss.

The transform never mutates input chunks in place; it returns new
``Chunk`` objects with ``metadata["images"]`` and ``metadata["image_refs"]"
filtered to content images only.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.settings import (
    ImageClassifierSettings,
    IngestionSettings,
)
from src.core.types import Chunk
from src.ingestion.transform.base_transform import BaseTransform

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext
    from src.libs.llm.base_llm import BaseLLM


logger = logging.getLogger(__name__)


# Metadata keys this transform writes.
META_IMAGES = "images"
META_IMAGE_REFS = "image_refs"
META_CLASSIFICATION_OVERRIDDEN = "image_classification_overridden"


# Built-in fallback prompt used when the configured file is missing.
_DEFAULT_PROMPT = """你是一位文档图片分析助手。给定以下文档片段和片段中提到的一张图片，请判断这张图片是"内容图"还是"装饰图"。

定义：
- 内容图（content）：包含信息性内容，如图表、流程图、截图、示意图、数据可视化、表格截图等，对理解文档有帮助。
- 装饰图（decorative）：不携带实质信息，如公司 logo、页眉页脚图标、装饰性边框、重复出现的相同图片等。

文档片段上下文：
{context}

请仅输出 JSON，不要添加任何解释或 Markdown 标记：
{"classification": "content" 或 "decorative", "reason": "简短理由"}"""


class ImageContentClassifier(BaseTransform):
    """
    Classify chunk-attached images as content or decorative.

    Args:
        settings: Either an :class:`IngestionSettings` (its
            ``image_classifier`` sub-config is used) or an
            :class:`ImageClassifierSettings` directly.
        llm: Optional vision-capable LLM client. Required when
            ``llm.enabled=True``; if absent or non-vision, the
            transform runs in hard-filter-only mode.
        prompt_path: Override for the prompt file location.
    """

    name = "image_classifier"

    def __init__(
        self,
        settings: IngestionSettings | ImageClassifierSettings,
        llm: "BaseLLM | None" = None,
        prompt_path: str | None = None,
    ) -> None:
        if isinstance(settings, IngestionSettings):
            self._cfg = settings.image_classifier
        else:
            self._cfg = settings
        self.llm = llm
        self._explicit_prompt_path = prompt_path
        self._prompt_template = self._load_prompt(self._cfg.llm.prompt_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def transform(
        self,
        chunks: list[Chunk],
        trace: "TraceContext | None" = None,
    ) -> list[Chunk]:
        if trace is not None:
            trace.record_stage(
                self.name,
                event="start",
                n_chunks=len(chunks),
                enabled=self._cfg.enabled,
                llm_enabled=bool(
                    self._cfg.llm.enabled
                    and self.llm is not None
                    and "vision" in self.llm.capabilities
                ),
            )

        if not self._cfg.enabled:
            if trace is not None:
                trace.record_stage(self.name, event="skipped", reason="disabled")
            return chunks

        out = [self._classify_one(c, trace) for c in chunks]

        if trace is not None:
            trace.record_stage(self.name, event="finish", n_out=len(out))
        return out

    # ------------------------------------------------------------------
    # Per-chunk classification (never raises)
    # ------------------------------------------------------------------
    def _classify_one(
        self,
        chunk: Chunk,
        trace: "TraceContext | None" = None,
    ) -> Chunk:
        images = chunk.metadata.get(META_IMAGES) or []
        image_refs = chunk.metadata.get(META_IMAGE_REFS) or []
        if not images or not image_refs:
            return chunk

        # Build a list of content images; skip hard-filtered decorative ones.
        content_images: list[dict[str, Any]] = []
        llm_judged_any = False

        for img in images:
            if not isinstance(img, dict):
                continue
            if img.get("is_content") is False:
                if trace is not None:
                    trace.record_stage(
                        self.name,
                        event="hard_filter_skip",
                        chunk_id=chunk.id,
                        image_id=img.get("id"),
                        reason=img.get("classification_reason"),
                    )
                continue

            # Optional LLM judgment for images that passed hard filters.
            if self._cfg.llm.enabled and self.llm is not None:
                if "vision" in self.llm.capabilities:
                    try:
                        is_content, reason = self._llm_classify(
                            chunk, img,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "ImageContentClassifier LLM call failed for %s: %s",
                            img.get("id"), exc,
                        )
                        if trace is not None:
                            trace.record_stage(
                                self.name,
                                event="llm_fallback",
                                chunk_id=chunk.id,
                                image_id=img.get("id"),
                                error=str(exc),
                            )
                        if not self._cfg.llm.fallback_on_error:
                            # Treat as content when we are not allowed to fall back.
                            is_content, reason = True, "llm_error_fallback"
                        else:
                            # Default fail-open: keep the image.
                            is_content, reason = True, "llm_error_fallback"
                    else:
                        llm_judged_any = True

                    if not is_content:
                        if trace is not None:
                            trace.record_stage(
                                self.name,
                                event="llm_decorative",
                                chunk_id=chunk.id,
                                image_id=img.get("id"),
                                reason=reason,
                            )
                        continue

                    if reason:
                        img = dict(img)
                        img["classification_reason"] = reason

            content_images.append(img)

        if len(content_images) == len(images):
            # Nothing changed; avoid copying metadata.
            return chunk

        new_refs = [img["id"] for img in content_images if img.get("id")]
        meta = copy.deepcopy(chunk.metadata)
        if content_images:
            meta[META_IMAGES] = content_images
            meta[META_IMAGE_REFS] = new_refs
        else:
            # Match DocumentChunker convention: no images → no keys.
            meta.pop(META_IMAGES, None)
            meta.pop(META_IMAGE_REFS, None)
        if llm_judged_any:
            meta[META_CLASSIFICATION_OVERRIDDEN] = True
        return replace(chunk, metadata=meta)

    # ------------------------------------------------------------------
    # LLM classification
    # ------------------------------------------------------------------
    def _llm_classify(
        self,
        chunk: Chunk,
        image: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """
        Ask the vision LLM whether ``image`` is content or decorative.

        Returns (is_content, reason). ``reason`` is ``None`` when the
        LLM declares the image as content without extra explanation.
        """
        assert self.llm is not None
        img_path = image.get("path")
        if not img_path or not Path(img_path).is_file():
            # No image file to show → keep it as content rather than
            # make a wrong decision without visual input.
            return True, "missing_image_file"

        from src.libs.llm.content_block import ImageBlock, TextBlock

        context = chunk.text or ""
        prompt = self._prompt_template.replace("{context}", context)
        prompt = prompt.replace("{text}", context)

        messages = [
            {
                "role": "user",
                "content": [
                    TextBlock(text=prompt).to_dict(),
                    ImageBlock(image=str(img_path)).to_dict(),
                ],
            }
        ]
        raw = self.llm.chat(messages)
        if not raw:
            return True, "empty_llm_response"

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        # Try JSON parse first.
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                classification = str(data.get("classification", "")).lower()
                reason = data.get("reason")
                is_content = classification in ("content", "内容图", "内容")
                return is_content, reason if isinstance(reason, str) else None
        except json.JSONDecodeError:
            pass

        # Fallback: keyword detection in raw text.
        lower = cleaned.lower()
        if any(k in lower for k in ("decorative", "装饰图", "装饰")):
            return False, "keyword_fallback: decorative"
        # If no clear decorative signal, treat as content to be safe.
        return True, "keyword_fallback: content"

    # ------------------------------------------------------------------
    # Prompt loading
    # ------------------------------------------------------------------
    def _load_prompt(self, path: str) -> str:
        p = Path(self._explicit_prompt_path or path)
        if p.is_file():
            return p.read_text(encoding="utf-8")
        logger.warning(
            "ImageContentClassifier prompt file not found: %s — "
            "using built-in default", p,
        )
        return _DEFAULT_PROMPT


__all__ = ["ImageContentClassifier"]
