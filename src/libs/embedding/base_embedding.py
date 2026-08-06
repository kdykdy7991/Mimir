"""
Embedding abstract base class.

Defines the unified interface for all Embedding providers (OpenAI, Azure, Ollama).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseEmbedding(ABC):
    """
    Abstract base class for Embedding providers.

    All Embedding implementations must inherit from this class
    and implement the `embed` method.
    """

    @abstractmethod
    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed.
            **kwargs: Additional provider-specific parameters.

        Returns:
            list[list[float]]: List of embedding vectors, one per input text.
                               Each vector is a list of floats.

        Raises:
            EmbeddingError: If the request fails.
        """
        pass

    def embed_single(self, text: str, **kwargs: Any) -> list[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text string to embed.
            **kwargs: Additional parameters.

        Returns:
            list[float]: Embedding vector.
        """
        results = self.embed([text], **kwargs)
        return results[0]

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """
        Return the dimensionality of the embedding vectors.

        Returns:
            int: Number of dimensions (e.g., 1536 for text-embedding-3-small).
        """
        pass


class EmbeddingError(Exception):
    """Base exception for Embedding-related errors."""
    pass


class EmbeddingConnectionError(EmbeddingError):
    """Raised when connection to Embedding provider fails."""
    pass


class EmbeddingRateLimitError(EmbeddingError):
    """Raised when rate limit is exceeded."""
    pass
