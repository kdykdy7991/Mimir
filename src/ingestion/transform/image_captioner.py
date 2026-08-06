"""
ImageCaptioner (C7) — optional Vision LLM captioning for image refs.

When ``use_llm=True`` AND a vision-capable LLM is injected AND the
chunk has ``image_refs`` in its metadata, this transform asks the
LLM for a short caption per image and writes the results back into
``chunk.metadata["image_captions"]``.

If the LLM is disabled / unavailable / raises, the chunk still goes
through the pipeline — its ``image_refs`` are preserved, and the
transform marks ``has_unprocessed_images = True`` so downstream
consumers (dashboard, MCP tools) know to surface a placeholder
rather than pretend a caption exists.

The transform never mutates the input chunks in place; it always
returns new :class:`Chunk` objects (consistent with C5 / C6).

Note
----
The current :class:`BaseLLM` interface is text-only; production
deployments wire a vision-capable implementation that accepts
``messages`` carrying image references (OpenAI's ``gpt-4o``,
Anthropic's ``claude-3.5-sonnet``, etc.). For tests, a mock LLM
that returns a string is sufficient.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.settings import (
    ImageCaptionerSettings,
    IngestionSettings,
)
from src.core.types import Chunk
from src.ingestion.transform.base_transform import BaseTransform

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext
    from src.libs.llm.base_llm import BaseLLM


logger = logging.getLogger(__name__)


# Metadata keys the captioner writes.
META_CAPTIONS = "image_captions"           # list[{id, caption}]
META_UNPROCESSED = "has_unprocessed_images"  # bool

# Per-caption value shape: {"id": str, "caption": str}
# We keep this loose (not a dataclass) to match the existing
# pattern in C4/C6 where chunks' metadata is plain dicts.


# Built-in fallback prompt used when the configured file is missing.
# Mirrors the structure of the real prompt so tests / first-time
# runs get something reasonable.
_DEFAULT_PROMPT = """你是一个擅长图片描述的助手。给定以下文档上下文(图片出现
的位置),请为图片生成一个简洁的描述。

文档上下文:
{context}

请输出图片描述:"""


class ImageCaptioner(BaseTransform):
    """
    Attach LLM-generated captions to chunks that reference images.

    Args:
        settings: An :class:`IngestionSettings` (uses its
            ``image_captioner`` sub-config) or an
            :class:`ImageCaptionerSettings` directly. The latter is
            convenient for tests.
        llm: Optional vision-capable LLM client. Required when
            ``use_llm=True``; if absent, the transform logs a
            warning and runs in degraded mode (chunks with images
            get ``has_unprocessed_images = True``).
        prompt_path: Override for the prompt file location.
    """

    name = "image_captioner"

    def __init__(
        self,
        settings: IngestionSettings | ImageCaptionerSettings,
        llm: "BaseLLM | None" = None,
        prompt_path: str | None = None,
    ) -> None:
        if isinstance(settings, IngestionSettings):
            self._cfg = settings.image_captioner
        else:
            self._cfg = settings
        self.llm = llm
        self._explicit_prompt_path = prompt_path
        self._prompt_template = self._load_prompt(self._cfg.prompt_path)

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
                use_llm=bool(self._cfg.use_llm and self.llm is not None),
            )
        out = [self._caption_one(c, trace) for c in chunks]
        if trace is not None:
            trace.record_stage(self.name, event="finish", n_out=len(out))
        return out

    # ------------------------------------------------------------------
    # Per-chunk processing (never raises)
    # ------------------------------------------------------------------
    def _caption_one(
        self,
        chunk: Chunk,
        trace: "TraceContext | None" = None,
    ) -> Chunk:
        image_refs: list[str] = list(
            chunk.metadata.get("image_refs") or []
        )
        if not image_refs:
            # Nothing to do — no images attached to this chunk.
            return chunk

        # Disabled OR no LLM wired → degraded mode.
        if not self._cfg.use_llm or self.llm is None:
            if trace is not None:
                trace.record_stage(
                    self.name, event="skipped",
                    chunk_id=chunk.id, reason="llm_disabled",
                )
            return self._mark_unprocessed(
                chunk, reason="llm_disabled_or_unavailable",
            )

        # Try to caption each image. Per-image failure is logged
        # but does NOT abort the whole chunk — we just mark the
        # failed images as unprocessed.
        captions: list[dict[str, str]] = []
        unprocessed: list[str] = []

        for img_id in image_refs:
            try:
                caption = self._llm_caption(chunk, img_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ImageCaptioner LLM call failed for %s: %s",
                    img_id, exc,
                )
                if not self._cfg.fallback_on_error:
                    raise
                unprocessed.append(img_id)
                continue
            if not caption:
                unprocessed.append(img_id)
                continue
            captions.append({
                "id": img_id,
                "caption": _truncate(caption, self._cfg.max_caption_len),
            })

        if trace is not None:
            trace.record_stage(
                self.name, event="captioned",
                chunk_id=chunk.id,
                n_captioned=len(captions),
                n_unprocessed=len(unprocessed),
            )

        return self._apply_captions(
            chunk, captions=captions, unprocessed=unprocessed,
        )

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------
    def _llm_caption(self, chunk: Chunk, image_id: str) -> str | None:
        """
        Build a prompt and call the LLM. Returns the caption text
        (or ``None`` if the LLM returned nothing usable).

        The image path is included so a vision-capable LLM can
        locate the file. The ``context`` placeholder is filled with
        the chunk's text so the LLM can relate the image to the
        surrounding document.
        """
        assert self.llm is not None
        # Find the image path if we have it.
        img_path = self._find_image_path(chunk, image_id)
        context = chunk.text or ""
        # Substitute both ``{context}`` and the path. The prompt
        # template's ``{context}`` is the user-facing variable;
        # we also substitute ``{image_path}`` if the prompt uses
        # it.
        prompt = self._prompt_template.replace("{context}", context)
        if img_path is not None:
            prompt = prompt.replace("{image_path}", str(img_path))
        # Some prompts may also use ``{text}`` as a generic alias.
        prompt = prompt.replace("{text}", context)
        result = self.llm.chat([{"role": "user", "content": prompt}])
        if not result:
            return None
        return result.strip()

    @staticmethod
    def _find_image_path(chunk: Chunk, image_id: str) -> str | None:
        """Look up an image's on-disk path inside the chunk metadata.

        If the chunk has ``images`` (a list of ``ImageRef``-shaped
        dicts) we look there; otherwise return ``None`` and the LLM
        is invoked without a path hint.
        """
        images = chunk.metadata.get("images")
        if not isinstance(images, list):
            return None
        for img in images:
            if isinstance(img, dict) and img.get("id") == image_id:
                return img.get("path")
        return None

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _apply_captions(
        chunk: Chunk,
        *,
        captions: list[dict[str, str]],
        unprocessed: list[str],
    ) -> Chunk:
        """Return a copy of ``chunk`` with caption metadata applied.

        Notes on key presence:
        - ``META_CAPTIONS`` is set ONLY when at least one caption
          was produced. An empty list means "we tried, nothing
          usable came back" and downstream code should treat the
          chunk as having no captions at all.
        - ``META_UNPROCESSED`` is set whenever any image is still
          uncaptioned (either the LLM failed for it or returned
          nothing).
        """
        meta = copy.deepcopy(chunk.metadata)
        if captions:
            meta[META_CAPTIONS] = captions
        if unprocessed:
            meta[META_UNPROCESSED] = True
        elif META_UNPROCESSED in meta:
            # Clean up stale marker if this run succeeded for all
            # of the chunk's images.
            del meta[META_UNPROCESSED]
        return replace(chunk, metadata=meta)

    @staticmethod
    def _mark_unprocessed(
        chunk: Chunk, *, reason: str
    ) -> Chunk:
        """Return a copy of ``chunk`` flagged as having images that
        still need captioning. Preserves ``image_refs`` so downstream
        consumers know what to surface."""
        meta = copy.deepcopy(chunk.metadata)
        meta[META_UNPROCESSED] = True
        meta.setdefault("image_captioner_skip_reason", reason)
        return replace(chunk, metadata=meta)

    # ------------------------------------------------------------------
    # Prompt loading
    # ------------------------------------------------------------------
    def _load_prompt(self, path: str) -> str:
        p = Path(self._explicit_prompt_path or path)
        if p.is_file():
            template = p.read_text(encoding="utf-8")
        else:
            logger.warning(
                "ImageCaptioner prompt file not found: %s — "
                "using built-in default", p,
            )
            template = _DEFAULT_PROMPT
        # We don't strictly require ``{context}`` here — different
        # prompts may use a different placeholder (``{text}``,
        # ``{chunk_text}``, etc.). The substitution step in
        # ``_llm_caption`` handles all known variants.
        return template


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(text: str, max_len: int) -> str:
    """Truncate to ``max_len`` characters, appending an ellipsis."""
    if max_len <= 0 or len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1].rstrip() + "…"
