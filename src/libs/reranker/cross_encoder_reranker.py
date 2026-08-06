"""
Cross-Encoder Reranker implementation.

Uses sentence-transformers CrossEncoder for local reranking.
Supports models like BGE-reranker-v2-m3 and Qwen3-Reranker.
"""

from __future__ import annotations

from typing import Any

from src.libs.reranker.base_reranker import (
    BaseReranker,
    RerankCandidate,
    RerankerError,
)

try:
    from sentence_transformers import CrossEncoder
except ImportError:
    CrossEncoder = None  # type: ignore


class CrossEncoderReranker(BaseReranker):
    """
    Cross-Encoder Reranker.

    Uses sentence-transformers CrossEncoder for local reranking.
    No external API calls needed.

    Supported models:
    - BAAI/bge-reranker-v2-m3 (multilingual, recommended)
    - Qwen/Qwen3-Reranker-0.6B (Chinese optimized, lightweight)
    - cross-encoder/ms-marco-MiniLM-L-6-v2 (English, lightweight)
    """

    def __init__(self, settings: Any):
        if CrossEncoder is None:
            raise RerankerError(
                "sentence-transformers package is not installed. "
                "Install it with: pip install sentence-transformers"
            )
        self.settings = settings
        self._model_name = settings.model
        self._device = getattr(settings, "device", "cpu")

        # Load model
        self._model = CrossEncoder(
            self._model_name,
            device=self._device,
        )

    def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        top_k: int | None = None,
        **kwargs: Any,
    ) -> list[RerankCandidate]:
        """
        Rerank candidates using Cross-Encoder model.

        Args:
            query: The original query.
            candidates: List of candidates to rerank.
            top_k: Optional limit on results.

        Returns:
            list[RerankCandidate]: Reranked candidates sorted by relevance.
        """
        if not candidates:
            return []

        try:
            # Prepare query-candidate pairs
            pairs = [(query, c.text) for c in candidates]

            # Get scores
            scores = self._model.predict(pairs)

            # Attach scores to candidates and sort
            scored_candidates = []
            for i, candidate in enumerate(candidates):
                # Normalize score to 0-1 range if needed
                score = float(scores[i])
                if score > 1.0:
                    score = 1.0
                elif score < 0.0:
                    score = 0.0
                scored_candidates.append(RerankCandidate(
                    id=candidate.id,
                    text=candidate.text,
                    score=score,
                    metadata=candidate.metadata,
                ))

            # Sort by score (descending)
            scored_candidates.sort(key=lambda x: x.score, reverse=True)

            # Apply top_k
            if top_k is not None:
                scored_candidates = scored_candidates[:top_k]

            return scored_candidates

        except RerankerError:
            raise
        except Exception as e:
            raise RerankerError(f"Cross-Encoder reranking failed: {e}") from e
