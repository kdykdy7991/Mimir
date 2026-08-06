"""
MetadataEnricher (C6) — generates ``title`` / ``summary`` / ``tags``
for each chunk and writes them into ``chunk.metadata``.

Two stages, mirroring C5's pattern:

1. **Rule-based** (always runs) — deterministic, zero-cost fall-
   back. The title is the first non-empty line (truncated); the
   summary is the first 1-2 sentences; the tags are the most
   frequent non-stopword tokens.
2. **LLM-based** (optional) — the rule result is sent to an LLM
   with a JSON-output prompt; the parsed JSON replaces the rule
   result. If the LLM call or the JSON parse fails, the rule
   result is kept and a fallback reason is recorded.

Failure handling
----------------
A failure in either stage for one chunk keeps the chunk in the
output stream — the pipeline never aborts on a single bad chunk.
The chunk's metadata always ends up with the three required fields
populated, so downstream consumers (retrieval, MCP tools, dashboard)
can rely on their presence.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.settings import (
    IngestionSettings,
    MetadataEnricherSettings,
)
from src.core.types import Chunk
from src.ingestion.transform.base_transform import BaseTransform

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext
    from src.libs.llm.base_llm import BaseLLM


logger = logging.getLogger(__name__)


# Metadata keys
META_TITLE = "title"
META_SUMMARY = "summary"
META_TAGS = "tags"
META_ENRICHED_BY = "enriched_by"
META_ENRICH_FALLBACK = "enrich_fallback_reason"

# Values for ``enriched_by``
ENRICHED_BY_RULE = "rule"
ENRICHED_BY_LLM = "llm"
ENRICHED_BY_LLM_FALLBACK = "rule"  # alias used together with the reason
ENRICHED_BY_ERROR = "error"        # rule pass itself failed

# A small built-in stopword list (English + a few common Chinese
# particles). Keeps rule-based tags focused on content words.
_STOPWORDS = frozenset(
    """
    a an the and or but if then else when while for to of in on at by
    with from as is are was were be been being have has had do does did
    this that these those it its their there here i you he she we they
    me him her us them my your our their what which who whom whose
    not no nor so too very can could may might will would shall should
    的 了 是 在 有 和 与 或 但 如果 那么 这 那 我 你 他 她 它 我们 你们
    他们 也 都 而 及 等 于 上 下 中
    """.split()
)

# Word-tokenizer: ASCII words, plus runs of CJK characters.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]+|[一-鿿]+")

# Sentence splitter: ``. ! ?`` followed by whitespace, or line break.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|(?<=\n)\s*")


class MetadataEnricher(BaseTransform):
    """
    Attach title / summary / tags to each chunk's metadata.

    Args:
        settings: Either an :class:`IngestionSettings` (its
            ``metadata_enricher`` sub-config is used) or a
            :class:`MetadataEnricherSettings` directly. The latter
            is convenient for tests.
        llm: Optional LLM client. Required when ``use_llm=True``.
        prompt_path: Override the prompt file location. Falls back
            to ``settings.prompt_path`` and finally a hard-coded
            default. The template MUST contain ``{chunk_text}``.
    """

    name = "metadata_enricher"

    def __init__(
        self,
        settings: IngestionSettings | MetadataEnricherSettings,
        llm: "BaseLLM | None" = None,
        prompt_path: str | None = None,
    ) -> None:
        if isinstance(settings, IngestionSettings):
            self._cfg = settings.metadata_enricher
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
        out = [self._enrich_one(c, trace) for c in chunks]
        if trace is not None:
            trace.record_stage(self.name, event="finish", n_out=len(out))
        return out

    # ------------------------------------------------------------------
    # Per-chunk enrichment (never raises)
    # ------------------------------------------------------------------
    def _enrich_one(
        self,
        chunk: Chunk,
        trace: "TraceContext | None" = None,
    ) -> Chunk:
        text = chunk.text or ""
        if not text.strip():
            return self._stamp(
                chunk,
                title="(empty)",
                summary="(empty chunk)",
                tags=[],
                enriched_by=ENRICHED_BY_RULE,
            )

        # Stage 1: rule-based — always runs.
        try:
            rule = self._rule_based_enrich(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "MetadataEnricher rule pass failed for %s: %s",
                chunk.id, exc,
            )
            if trace is not None:
                trace.record_stage(
                    self.name, event="rule_error",
                    chunk_id=chunk.id, error=str(exc),
                )
            return self._stamp(
                chunk,
                title="(error)",
                summary="(rule-based enrichment failed)",
                tags=[],
                enriched_by=ENRICHED_BY_ERROR,
                extra={"enrich_rule_error": str(exc)},
            )

        # Stage 2: optional LLM
        if self._cfg.use_llm and self.llm is not None:
            try:
                llm_result = self._llm_enrich(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MetadataEnricher LLM pass failed for %s: %s",
                    chunk.id, exc,
                )
                if trace is not None:
                    trace.record_stage(
                        self.name, event="llm_fallback",
                        chunk_id=chunk.id, error=str(exc),
                    )
                return self._stamp(
                    chunk, **rule, enriched_by=ENRICHED_BY_LLM_FALLBACK,
                    extra={META_ENRICH_FALLBACK: str(exc)},
                )

            if llm_result is not None:
                return self._stamp(
                    chunk,
                    **llm_result,
                    enriched_by=ENRICHED_BY_LLM,
                )

            # LLM returned unusable output → keep rule result, mark fallback.
            if trace is not None:
                trace.record_stage(
                    self.name, event="llm_fallback",
                    chunk_id=chunk.id,
                    error="invalid_llm_output",
                )
            return self._stamp(
                chunk, **rule, enriched_by=ENRICHED_BY_LLM_FALLBACK,
                extra={META_ENRICH_FALLBACK: "invalid_llm_output"},
            )

        # LLM disabled → rule-only.
        return self._stamp(chunk, **rule, enriched_by=ENRICHED_BY_RULE)

    # ------------------------------------------------------------------
    # Rule-based enrichment
    # ------------------------------------------------------------------
    def _rule_based_enrich(self, text: str) -> dict[str, Any]:
        """Return ``{title, summary, tags}`` derived from the text.

        Title:   first non-empty line, trimmed and truncated.
        Summary: first 1-2 sentences, trimmed and truncated.
        Tags:    top-N most frequent non-stopword tokens.
        """
        cleaned = text.strip()
        lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
        title = (lines[0] if lines else cleaned[: self._cfg.max_title_len])
        title = _truncate(title, self._cfg.max_title_len)

        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(cleaned) if s.strip()]
        if not sentences:
            summary = title
        else:
            summary = " ".join(sentences[:2])
        summary = _truncate(summary, self._cfg.max_summary_len)

        tags = self._extract_tags(cleaned)
        return {"title": title, "summary": summary, "tags": tags}

    def _extract_tags(self, text: str) -> list[str]:
        """Top-N most frequent non-stopword tokens, lowercased + deduped
        in first-seen order.
        """
        counter: Counter[str] = Counter()
        seen_order: list[str] = []
        for tok in _TOKEN_RE.findall(text):
            key = tok.lower()
            if key in _STOPWORDS:
                continue
            if len(key) < 2:  # skip single chars / digits
                continue
            if key not in counter:
                seen_order.append(key)
            counter[key] += 1
        # Sort by frequency desc, then by first-seen order.
        ranked = sorted(
            seen_order,
            key=lambda k: (-counter[k], seen_order.index(k)),
        )
        return ranked[: self._cfg.max_tags]

    # ------------------------------------------------------------------
    # LLM enrichment
    # ------------------------------------------------------------------
    def _llm_enrich(self, text: str) -> dict[str, Any] | None:
        """Call the LLM, parse the JSON response, validate fields.

        Returns ``None`` if the response is not a valid JSON object
        with the required keys. Raises on transport / API errors
        (caller is responsible for fallback handling).
        """
        assert self.llm is not None
        prompt = self._prompt_template.replace("{chunk_text}", text)
        prompt = prompt.replace("{text}", text)
        raw = self.llm.chat([{"role": "user", "content": prompt}])
        if not raw:
            return None
        # Strip Markdown code fences the LLM sometimes wraps around JSON.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = _strip_code_fence(cleaned)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        # Pull out the three fields, tolerate missing ones by returning
        # None so the caller can fall back to rule output.
        title = data.get("title")
        summary = data.get("summary")
        tags = data.get("tags")
        if not (isinstance(title, str) and title
                and isinstance(summary, str) and summary):
            return None
        if not isinstance(tags, list):
            return None
        # Normalize tags to list[str].
        norm_tags = [str(t).strip() for t in tags if str(t).strip()]
        return {
            "title": _truncate(title, self._cfg.max_title_len),
            "summary": _truncate(summary, self._cfg.max_summary_len),
            "tags": norm_tags[: self._cfg.max_tags],
        }

    # ------------------------------------------------------------------
    # Prompt loading
    # ------------------------------------------------------------------
    def _load_prompt(self, path: str) -> str:
        p = Path(self._explicit_prompt_path or path)
        if p.is_file():
            template = p.read_text(encoding="utf-8")
        else:
            logger.warning(
                "MetadataEnricher prompt file not found: %s — "
                "using built-in default", p,
            )
            template = _DEFAULT_PROMPT
        if "{chunk_text}" not in template and "{text}" not in template:
            raise ValueError(
                f"MetadataEnricher prompt at {p} contains neither "
                "'{{chunk_text}}' nor '{{text}}' placeholder"
            )
        return template

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _stamp(
        chunk: Chunk,
        *,
        title: str,
        summary: str,
        tags: list[str],
        enriched_by: str,
        extra: dict[str, Any] | None = None,
    ) -> Chunk:
        meta = copy.deepcopy(chunk.metadata)
        meta[META_TITLE] = title
        meta[META_SUMMARY] = summary
        meta[META_TAGS] = list(tags)
        meta[META_ENRICHED_BY] = enriched_by
        if extra:
            meta.update(extra)
        return replace(chunk, metadata=meta)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(text: str, max_len: int) -> str:
    """Truncate to ``max_len`` characters, appending an ellipsis if cut."""
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1].rstrip() + "…"


def _strip_code_fence(text: str) -> str:
    """Remove a leading/trailing Markdown code fence if present."""
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Built-in fallback prompt (used when the configured file is missing).
# ---------------------------------------------------------------------------

_DEFAULT_PROMPT = """你是一个文档元数据生成助手。给定一个从长文档中切分出来的
文本片段,根据其内容生成结构化元数据。

字段说明:
- title: 一句话标题,概括该片段的核心主题,不超过 80 字符
- summary: 1-3 句摘要,提取关键信息和论点,不超过 200 字符
- tags: 3-5 个关键词,小写、简短,用于检索过滤和聚类

要求:
1. 仅基于片段内容生成,不得引入外部知识
2. 保持原文语言
3. 严格输出 JSON 格式,不要包含额外说明或 Markdown 标记

输入片段:
{chunk_text}

输出 JSON:
"""
