"""
ChunkRefiner — first transform in the ingestion pipeline.

Two-stage refinement:

1. **Rule-based** (always runs) — strips noise that PDFs and HTML
   scrapers commonly leave behind (excess whitespace, page headers
   and footers, HTML comments, Markdown image syntax, horizontal
   rules). Code blocks (triple-backtick fences) and Markdown
   structure (headings, lists, blockquotes) are left untouched.
2. **LLM-based** (optional) — hands the rule-cleaned text to an LLM
   for a semantic cleanup pass: turn fragmented OCR output into
   coherent sentences, fill minimal context, etc.

Failure handling
----------------
* A rule-based failure for a single chunk keeps the original text
  and tags the chunk ``refined_by="error"`` (the pipeline never
  aborts on one bad chunk).
* An LLM failure (timeout, 4xx/5xx, malformed response) falls back
  to the rule-based result and tags the chunk ``refined_by="rule"``
  with a ``refine_fallback_reason`` metadata field. The pipeline
  never aborts on an LLM outage.
"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.settings import ChunkRefinerSettings, IngestionSettings
from src.core.types import Chunk
from src.ingestion.transform.base_transform import BaseTransform

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext
    from src.libs.llm.base_llm import BaseLLM


logger = logging.getLogger(__name__)


# Metadata keys the refiner writes onto each chunk. Centralized so
# downstream code (C6, C7) and tests can reference the same names.
META_REFINED_BY = "refined_by"
META_REFINED_AT = "refined_at"

# Values for ``refined_by``:
REFINED_BY_RULE = "rule"          # only the rule pass ran
REFINED_BY_LLM = "llm"            # LLM pass succeeded
REFINED_BY_LLM_FALLBACK = "rule"  # LLM attempted, failed → kept rule result;
                                  # alias used together with refine_fallback_reason
REFINED_BY_ERROR = "error"        # rule pass itself failed; original text kept
REFINED_BY_SKIPPED = "skipped"    # no-op (empty input, etc.)

# Extra metadata fields
META_FALLBACK_REASON = "refine_fallback_reason"
META_RULE_ERROR = "refine_rule_error"


# ---------------------------------------------------------------------------
# Rule patterns
# ---------------------------------------------------------------------------

# Pre-compiled at import time so the refiner is hot-loop friendly.

# 1. Collapse 3+ newlines down to 2 (a single paragraph break).
_RE_BLANK_LINES = re.compile(r"\n{3,}")

# 2. Collapse runs of horizontal whitespace (spaces, tabs) inside a
#    single line to a single space. Applied per-line to keep the
#    ``\n`` boundaries intact.
_RE_INLINE_WS = re.compile(r"[ \t]+")

# 3. Trailing whitespace on every line.
_RE_TRAILING_WS = re.compile(r"[ \t]+$", flags=re.MULTILINE)

# 4. HTML comments: <!-- ... --> (DOTALL so ``\n`` inside is consumed).
_RE_HTML_COMMENT = re.compile(r"<!--.*?-->", flags=re.DOTALL)

# 5. Markdown image syntax: ![alt](url)
_RE_MD_IMAGE = re.compile(r"!\[.*?\]\([^)]*\)")

# 6. Horizontal-rule lines: ---, ***, ___ (3+ chars, possibly with spaces).
_RE_HR_LINE = re.compile(r"^\s*[-*_]{3,}\s*$", flags=re.MULTILINE)

# 7. Page-number patterns on their own line:
#    "Page 1 of 10"  |  "Page 1"  |  "- 5 -"  |  "5"
_RE_PAGE_NUMBER = re.compile(
    r"^\s*(?:[-–—]\s*\d+\s*[-–—]|\d+|Page\s+\d+(?:\s+of\s+\d+)?)\s*$",
    flags=re.MULTILINE | re.IGNORECASE,
)

# 8. Typical page-header patterns: short lines that look like a
#    document title repeated on every page, e.g.
#    "Document Title | Confidential" or "ACME — Annual Report 2024".
#    We keep the rule conservative — only matches when the line is
#    short (< 80 chars) and has NO sentence-ending punctuation.
_RE_HEADER_LINE = re.compile(
    r"^[A-Z][\w \-,&|]{2,80}$",
    flags=re.MULTILINE,
)

# 9. Fenced code blocks ```lang\n...\n``` — protected from any
#    other rule that might touch their contents.
_RE_CODE_FENCE = re.compile(
    r"```[\w+-]*\n.*?```",
    flags=re.DOTALL,
)


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------

class ChunkRefiner(BaseTransform):
    """
    Rule-based + optional LLM chunk refinement.

    Args:
        settings: Either an :class:`IngestionSettings` (its
            ``chunk_refiner`` sub-config is used) or a
            :class:`ChunkRefinerSettings` directly. The latter is
            useful for tests.
        llm: Optional LLM client. If ``None`` and ``settings.use_llm``
            is True, the refiner logs a warning and runs in
            rule-only mode.
        prompt_path: Override for the prompt file location. If
            ``None``, falls back to ``settings.prompt_path`` and
            finally a hard-coded default. The prompt template MUST
            contain ``{chunk_text}`` (or ``{text}``) — the refiner
            substitutes the chunk's text into it.
    """

    name = "chunk_refiner"

    def __init__(
        self,
        settings: IngestionSettings | ChunkRefinerSettings,
        llm: "BaseLLM | None" = None,
        prompt_path: str | None = None,
    ) -> None:
        if isinstance(settings, IngestionSettings):
            self._cfg = settings.chunk_refiner
        else:
            self._cfg = settings
        self.llm = llm
        self._explicit_prompt_path = prompt_path
        # Eagerly load the prompt (with fallback) so we fail fast
        # if the file is missing / malformed.
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

        out: list[Chunk] = []
        for chunk in chunks:
            out.append(self._refine_one(chunk, trace))

        if trace is not None:
            trace.record_stage(self.name, event="finish", n_out=len(out))
        return out

    # ------------------------------------------------------------------
    # Per-chunk refinement (never raises)
    # ------------------------------------------------------------------
    def _refine_one(
        self,
        chunk: Chunk,
        trace: "TraceContext | None" = None,
    ) -> Chunk:
        # Skip empty chunks — nothing to refine.
        if not chunk.text or not chunk.text.strip():
            return self._stamp(chunk, REFINED_BY_SKIPPED)

        # Stage 1: rule-based
        try:
            rule_text = self._rule_based_refine(chunk.text)
        except Exception as exc:  # noqa: BLE001 — never abort on one chunk
            logger.warning(
                "ChunkRefiner rule pass failed for %s: %s", chunk.id, exc
            )
            if trace is not None:
                trace.record_stage(
                    self.name, event="rule_error", chunk_id=chunk.id,
                    error=str(exc),
                )
            stamped = self._stamp(
                chunk, REFINED_BY_ERROR,
                extra={META_RULE_ERROR: str(exc)},
            )
            return stamped

        # Stage 2: optional LLM pass
        if self._cfg.use_llm and self.llm is not None:
            try:
                llm_text = self._llm_refine(rule_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ChunkRefiner LLM pass failed for %s: %s",
                    chunk.id, exc,
                )
                if trace is not None:
                    trace.record_stage(
                        self.name, event="llm_fallback",
                        chunk_id=chunk.id, error=str(exc),
                    )
                if self._cfg.fallback_on_error:
                    return self._stamp(
                        chunk, REFINED_BY_LLM_FALLBACK,
                        text=rule_text,
                        extra={META_FALLBACK_REASON: str(exc)},
                    )
                # fallback disabled → keep rule result anyway, but
                # mark the fallback so the caller can audit.
                return self._stamp(
                    chunk, REFINED_BY_LLM_FALLBACK,
                    text=rule_text,
                    extra={
                        META_FALLBACK_REASON:
                            f"fallback_on_error=False but LLM failed: {exc}",
                    },
                )

            # LLM returned a usable answer; use it (with sanity check).
            if llm_text and self._is_meaningfully_different(rule_text, llm_text):
                return self._stamp(chunk, REFINED_BY_LLM, text=llm_text)

            # LLM returned None / empty / identical-to-input → keep rule result
            if trace is not None:
                trace.record_stage(
                    self.name, event="llm_noop",
                    chunk_id=chunk.id,
                )
            return self._stamp(
                chunk, REFINED_BY_LLM_FALLBACK,
                text=rule_text,
                extra={META_FALLBACK_REASON: "llm_returned_no_change"},
            )

        # LLM disabled → rule-only
        return self._stamp(chunk, REFINED_BY_RULE, text=rule_text)

    # ------------------------------------------------------------------
    # Rule-based refinement
    # ------------------------------------------------------------------
    def _rule_based_refine(self, text: str) -> str:
        """
        Apply the noise-removal rules.

        Code blocks (triple-backtick fences) are extracted first and
        re-inserted at the end so other rules can't touch their
        internal whitespace.
        """
        # 1. Carve out code fences (and remember the original offsets).
        fences: list[str] = []
        def _stash(m: re.Match[str]) -> str:
            fences.append(m.group(0))
            return f"\x00FENCE_{len(fences) - 1}\x00"
        protected = _RE_CODE_FENCE.sub(_stash, text)

        # 2. HTML comments + Markdown images → remove.
        protected = _RE_HTML_COMMENT.sub("", protected)
        protected = _RE_MD_IMAGE.sub("", protected)

        # 3. Horizontal-rule lines → remove.
        protected = _RE_HR_LINE.sub("", protected)

        # 4. Page-number lines → remove.
        protected = _RE_PAGE_NUMBER.sub("", protected)

        # 5. Header lines (only when the line is the ENTIRE line,
        #    already enforced by the MULTILINE flag).
        protected = _RE_HEADER_LINE.sub("", protected)

        # 6. Per-line cleanup: strip trailing whitespace, then
        #    collapse internal multi-space → single space.
        protected = _RE_TRAILING_WS.sub("", protected)
        protected = "\n".join(
            _RE_INLINE_WS.sub(" ", line).strip()
            for line in protected.split("\n")
        )

        # 7. Collapse 3+ blank lines to 2.
        protected = _RE_BLANK_LINES.sub("\n\n", protected)

        # 8. Re-insert code fences.
        for i, fence in enumerate(fences):
            protected = protected.replace(f"\x00FENCE_{i}\x00", fence)

        return protected.strip()

    # ------------------------------------------------------------------
    # LLM refinement
    # ------------------------------------------------------------------
    def _llm_refine(self, text: str) -> str | None:
        """
        Call the LLM. Returns the cleaned text or ``None`` if the
        LLM signal is unusable (empty / exception).

        The caller is responsible for wrapping this in a try/except.
        """
        assert self.llm is not None  # invariant — caller checks
        prompt = self._prompt_template.replace("{chunk_text}", text)
        prompt = prompt.replace("{text}", text)  # tolerate either placeholder
        # BaseLLM.chat returns a string (content from the first choice).
        # The LLM layer is responsible for surfacing API errors as
        # exceptions; we just consume the result.
        result = self.llm.chat([{"role": "user", "content": prompt}])
        if not result:
            return None
        return result.strip()

    def _is_meaningfully_different(self, original: str, candidate: str) -> bool:
        """Return True if the LLM output is non-trivially different
        from the rule-cleaned input. Used to discard LLM no-ops.
        """
        if not candidate:
            return False
        if candidate == original:
            return False
        # Length-based sanity check: if the candidate is less than
        # ``min_change_ratio`` of the original length, treat as no-op.
        ratio = abs(len(candidate) - len(original)) / max(len(original), 1)
        return ratio >= self._cfg.min_change_ratio

    # ------------------------------------------------------------------
    # Prompt loading
    # ------------------------------------------------------------------
    def _load_prompt(self, path: str) -> str:
        """Load the prompt template from disk. Falls back to a
        hard-coded default if the file is missing. Raises if the
        file is malformed (no recognized placeholder).
        """
        p = Path(self._explicit_prompt_path or path)
        if p.is_file():
            template = p.read_text(encoding="utf-8")
        else:
            logger.warning(
                "ChunkRefiner prompt file not found: %s — using built-in",
                p,
            )
            template = _DEFAULT_PROMPT

        if "{chunk_text}" not in template and "{text}" not in template:
            raise ValueError(
                f"ChunkRefiner prompt at {p} contains neither "
                "'{{chunk_text}}' nor '{{text}}' placeholder"
            )
        return template

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _stamp(
        chunk: Chunk,
        refined_by: str,
        *,
        text: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Chunk:
        """Return a copy of ``chunk`` with refined text + new metadata."""
        meta = copy.deepcopy(chunk.metadata)
        meta[META_REFINED_BY] = refined_by
        if extra:
            meta.update(extra)
        return replace(
            chunk,
            text=text if text is not None else chunk.text,
            metadata=meta,
        )


# ---------------------------------------------------------------------------
# Built-in fallback prompt (used only if the configured file is missing).
# ---------------------------------------------------------------------------

_DEFAULT_PROMPT = """你是一个文本精炼助手。给定从文档中提取的原始文本片段,请将其精炼为
一个自包含、语义完整的知识单元。

**指令：**
1. 去噪：移除页眉、页脚、乱码字符或无意义的内容。
2. 保持连贯：如果该片段是更大段落的一部分,请补充最少的上下文使其独立可理解。
3. 保留原意：不得更改技术内容,不得添加原文未隐含的信息。
4. 保持原文语言:输出语言应与文档原文一致。

**输入片段:**
{chunk_text}

**精炼后的输出:**
"""
