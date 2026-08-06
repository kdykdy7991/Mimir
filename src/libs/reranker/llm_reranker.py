"""
LLM Reranker implementation.

Uses LLM to rerank candidates based on relevance to query.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.libs.reranker.base_reranker import (
    BaseReranker,
    RerankCandidate,
    RerankerError,
)

# Default rerank prompt path
_DEFAULT_PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "config" / "prompts" / "rerank.txt"


class LLMReranker(BaseReranker):
    """
    LLM-based Reranker.

    Uses LLM to score and rerank candidates based on query relevance.
    """

    def __init__(self, settings: Any, llm_client: Any = None):
        """
        Initialize LLM Reranker.

        Args:
            settings: Rerank settings.
            llm_client: LLM client instance (must have chat() method).
                       If None, uses settings.llm_client.
        """
        self.settings = settings
        self._llm_client = llm_client
        self._prompt_template = self._load_prompt()

    def _load_prompt(self) -> str:
        """Load rerank prompt template."""
        prompt_path = getattr(self.settings, "prompt_path", _DEFAULT_PROMPT_PATH)
        try:
            return Path(prompt_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            # Fallback to default prompt
            return self._get_default_prompt()

    def _get_default_prompt(self) -> str:
        """Default rerank prompt."""
        return """你是一个排序助手。给定一个查询和一组文档片段（每个片段有唯一 ID），请根据与查询的相关程度对片段进行排序。

**查询：**
{query}

**文档片段：**
{chunks}

**指令：**
1. 评估每个片段与查询的相关程度。
2. 按相关程度从高到低排序，返回 JSON 数组。
3. 仅保留真正相关的片段，完全无关的请排除。

**输出格式（仅输出 JSON，不要解释）：**
[
  {{"id": "<片段ID>", "relevance_score": <0.0-1.0>}},
  ...
]"""

    def _format_chunks(self, candidates: list[RerankCandidate]) -> str:
        """Format candidates for prompt."""
        lines = []
        for c in candidates:
            lines.append(f"[ID: {c.id}] {c.text}")
        return "\n\n".join(lines)

    def _parse_response(self, response: str) -> list[dict]:
        """Parse LLM response to extract ranked IDs."""
        try:
            # Try to extract JSON from response
            start = response.find("[")
            end = response.rfind("]") + 1
            if start != -1 and end > start:
                json_str = response[start:end]
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass
        return []

    def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        top_k: int | None = None,
        **kwargs: Any,
    ) -> list[RerankCandidate]:
        """
        Rerank candidates using LLM.

        Args:
            query: The original query.
            candidates: List of candidates to rerank.
            top_k: Optional limit on results.

        Returns:
            list[RerankCandidate]: Reranked candidates.
        """
        if not candidates:
            return []

        if self._llm_client is None:
            raise RerankerError("LLM client not provided")

        try:
            # Format prompt
            chunks_text = self._format_chunks(candidates)
            prompt = self._prompt_template.format(
                query=query,
                chunks=chunks_text,
            )

            # Call LLM
            response = self._llm_client.chat(
                [{"role": "user", "content": prompt}]
            )

            # Parse response
            ranked_items = self._parse_response(response)

            # Map ID to candidate
            candidate_map = {c.id: c for c in candidates}

            # Build result based on LLM ranking
            result = []
            seen_ids = set()

            for item in ranked_items:
                item_id = item.get("id")
                if item_id and item_id in candidate_map and item_id not in seen_ids:
                    candidate = candidate_map[item_id]
                    # Update score if provided
                    if "relevance_score" in item:
                        candidate.score = item["relevance_score"]
                    result.append(candidate)
                    seen_ids.add(item_id)

            # Add any candidates not ranked by LLM (at the end)
            for c in candidates:
                if c.id not in seen_ids:
                    result.append(c)

            # Apply top_k
            if top_k is not None:
                result = result[:top_k]

            return result

        except RerankerError:
            raise
        except Exception as e:
            raise RerankerError(f"LLM reranking failed: {e}") from e
