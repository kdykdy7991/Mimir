"""
Reranker abstract base class.

Defines the unified interface for all reranking strategies
(None, CrossEncoder, LLM).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RerankCandidate:
    """A candidate item for reranking."""
    id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseReranker(ABC):
    """
    Abstract base class for rerankers.

    All Reranker implementations must inherit from this class
    and implement the `rerank` method.
    """

    @abstractmethod
    def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        top_k: int | None = None,
        **kwargs: Any,
    ) -> list[RerankCandidate]:
        """
        Rerank candidates based on relevance to query.

        Args:
            query: The original query string.
            candidates: List of RerankCandidate objects to rerank.
            top_k: Optional limit on number of results to return.
            **kwargs: Additional parameters.

        Returns:
            list[RerankCandidate]: Reranked list of candidates.

        Raises:
            RerankerError: If reranking fails.
        """
        pass


class NoneReranker(BaseReranker):
    """
    No-op reranker that preserves original order.

    Used as default fallback when no reranking is configured.
    """

    def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        top_k: int | None = None,
        **kwargs: Any,
    ) -> list[RerankCandidate]:
        """Return candidates in original order."""
        if top_k is not None:
            return candidates[:top_k]
        return candidates


class RerankerError(Exception):
    """Base exception for Reranker-related errors."""
    pass
