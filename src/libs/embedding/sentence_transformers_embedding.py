"""
Sentence-Transformers Embedding implementation.

Uses sentence-transformers library for local embedding inference.
Supports all open-source embedding models from Hugging Face Hub.
"""

from __future__ import annotations

from typing import Any

from src.libs.embedding.base_embedding import (
    BaseEmbedding,
    EmbeddingError,
)

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None  # type: ignore


class SentenceTransformersEmbedding(BaseEmbedding):
    """
    Sentence-Transformers Embedding provider.

    Uses sentence-transformers library for local embedding inference.
    Supports GPU acceleration via device parameter.

    Supported models (examples):
    - BAAI/bge-small-zh-v1.5
    - BAAI/bge-large-zh-v1.5
    - nomic-ai/nomic-embed-text-v1
    - mixedbread-ai/mxbai-embed-large-v1
    - sentence-transformers/all-MiniLM-L6-v2
    """

    def __init__(self, settings: Any):
        if SentenceTransformer is None:
            raise EmbeddingError(
                "sentence-transformers package is not installed. "
                "Install it with: pip install sentence-transformers"
            )
        self.settings = settings
        self._model_name = settings.model
        self._device = getattr(settings, "device", "cpu")
        self._dimensions = settings.dimensions

        # Load model (lazy loading could be added here)
        self._model = SentenceTransformer(
            self._model_name,
            device=self._device,
        )

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed.
            **kwargs: Additional parameters (e.g., batch_size, show_progress_bar).

        Returns:
            list[list[float]]: List of embedding vectors.

        Raises:
            EmbeddingError: If embedding fails.
        """
        if not texts:
            return []

        try:
            embeddings = self._model.encode(
                texts,
                batch_size=kwargs.get("batch_size", 32),
                show_progress_bar=kwargs.get("show_progress_bar", False),
                convert_to_numpy=True,
            )
            return embeddings.tolist()
        except Exception as e:
            raise EmbeddingError(
                f"Sentence-Transformers embedding failed: {e}"
            ) from e

    @property
    def dimensions(self) -> int:
        """Return embedding dimensions."""
        return self._dimensions
