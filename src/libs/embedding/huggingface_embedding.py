"""
Hugging Face Transformers Embedding implementation.

Uses transformers library directly for embedding inference.
More flexible than Sentence-Transformers but requires more code.
"""

from __future__ import annotations

from typing import Any

from src.libs.embedding.base_embedding import (
    BaseEmbedding,
    EmbeddingError,
)

try:
    import torch
    from transformers import AutoModel, AutoTokenizer
except ImportError:
    torch = None  # type: ignore
    AutoModel = None  # type: ignore
    AutoTokenizer = None  # type: ignore


class HuggingFaceEmbedding(BaseEmbedding):
    """
    Hugging Face Transformers Embedding provider.

    Uses transformers library directly for more control over:
    - Pooling strategy (CLS, mean, last token)
    - Normalization
    - Quantization (INT8, FP16)

    For simpler usage, prefer SentenceTransformersEmbedding.
    """

    def __init__(self, settings: Any):
        if AutoModel is None or AutoTokenizer is None:
            raise EmbeddingError(
                "transformers and torch packages are not installed. "
                "Install them with: pip install transformers torch"
            )
        self.settings = settings
        self._model_name = settings.model
        self._device = getattr(settings, "device", "cpu")
        self._dimensions = settings.dimensions

        # Load model and tokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
        self._model = AutoModel.from_pretrained(self._model_name).to(self._device)
        self._model.eval()

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed.
            **kwargs: Additional parameters.

        Returns:
            list[list[float]]: List of embedding vectors.

        Raises:
            EmbeddingError: If embedding fails.
        """
        if not texts:
            return []

        try:
            # Tokenize
            inputs = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                return_tensors="pt",
            ).to(self._device)

            # Forward pass
            with torch.no_grad():
                outputs = self._model(**inputs)

            # CLS pooling (first token)
            embeddings = outputs.last_hidden_state[:, 0, :]

            # Normalize
            embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)

            return embeddings.cpu().tolist()
        except Exception as e:
            raise EmbeddingError(
                f"HuggingFace embedding failed: {e}"
            ) from e

    @property
    def dimensions(self) -> int:
        """Return embedding dimensions."""
        return self._dimensions
