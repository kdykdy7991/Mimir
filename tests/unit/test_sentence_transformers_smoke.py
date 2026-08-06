"""
Smoke tests for Sentence-Transformers and HuggingFace Embedding providers.

Tests use mock to avoid loading real models.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.core.settings import EmbeddingSettings
from src.libs.embedding.base_embedding import BaseEmbedding, EmbeddingError


# ---------------------------------------------------------------------------
# Mock helper
# ---------------------------------------------------------------------------

def mock_sentence_transformer():
    """Create a mock SentenceTransformer."""
    mock_model = MagicMock()
    # Return numpy array like real SentenceTransformer
    mock_model.encode.return_value = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    return mock_model


# ---------------------------------------------------------------------------
# Tests: Sentence-Transformers Embedding
# ---------------------------------------------------------------------------

class TestSentenceTransformersEmbedding:
    """Test Sentence-Transformers Embedding provider with mock."""

    @patch("src.libs.embedding.sentence_transformers_embedding.SentenceTransformer")
    def test_embed_returns_vectors(self, mock_st_cls):
        """embed() returns list of vectors."""
        mock_model = mock_sentence_transformer()
        mock_st_cls.return_value = mock_model

        settings = EmbeddingSettings(
            provider="sentence_transformers",
            model="BAAI/bge-small-zh-v1.5",
            dimensions=3,
        )
        from src.libs.embedding.sentence_transformers_embedding import SentenceTransformersEmbedding
        emb = SentenceTransformersEmbedding(settings)

        result = emb.embed(["hello", "world"])
        assert isinstance(result, list)
        assert len(result) == 2
        assert len(result[0]) == 3

    @patch("src.libs.embedding.sentence_transformers_embedding.SentenceTransformer")
    def test_embed_empty_list(self, mock_st_cls):
        """embed() handles empty input."""
        settings = EmbeddingSettings(
            provider="sentence_transformers",
            model="BAAI/bge-small-zh-v1.5",
            dimensions=512,
        )
        from src.libs.embedding.sentence_transformers_embedding import SentenceTransformersEmbedding
        emb = SentenceTransformersEmbedding(settings)

        result = emb.embed([])
        assert result == []
        # Should not call model.encode
        mock_st_cls.return_value.encode.assert_not_called()

    @patch("src.libs.embedding.sentence_transformers_embedding.SentenceTransformer")
    def test_dimensions_property(self, mock_st_cls):
        """dimensions property returns correct value."""
        settings = EmbeddingSettings(
            provider="sentence_transformers",
            model="BAAI/bge-small-zh-v1.5",
            dimensions=768,
        )
        from src.libs.embedding.sentence_transformers_embedding import SentenceTransformersEmbedding
        emb = SentenceTransformersEmbedding(settings)

        assert emb.dimensions == 768

    @patch("src.libs.embedding.sentence_transformers_embedding.SentenceTransformer")
    def test_uses_device_from_settings(self, mock_st_cls):
        """Uses device from settings."""
        settings = EmbeddingSettings(
            provider="sentence_transformers",
            model="BAAI/bge-small-zh-v1.5",
            dimensions=512,
            device="cuda",
        )

        from src.libs.embedding.sentence_transformers_embedding import SentenceTransformersEmbedding
        SentenceTransformersEmbedding(settings)

        call_kwargs = mock_st_cls.call_args.kwargs
        assert call_kwargs["device"] == "cuda"

    @patch("src.libs.embedding.sentence_transformers_embedding.SentenceTransformer")
    def test_encode_passes_parameters(self, mock_st_cls):
        """encode() passes batch_size and show_progress_bar."""
        mock_model = mock_sentence_transformer()
        mock_st_cls.return_value = mock_model

        settings = EmbeddingSettings(
            provider="sentence_transformers",
            model="BAAI/bge-small-zh-v1.5",
            dimensions=3,
        )
        from src.libs.embedding.sentence_transformers_embedding import SentenceTransformersEmbedding
        emb = SentenceTransformersEmbedding(settings)

        emb.embed(["hello"], batch_size=16, show_progress_bar=True)
        call_kwargs = mock_model.encode.call_args.kwargs
        assert call_kwargs["batch_size"] == 16
        assert call_kwargs["show_progress_bar"] is True


# ---------------------------------------------------------------------------
# Tests: Factory routing
# ---------------------------------------------------------------------------

class TestProviderRouting:
    """Test that factory routes to correct provider class."""

    @patch("src.libs.embedding.sentence_transformers_embedding.SentenceTransformer")
    def test_factory_routes_to_sentence_transformers(self, mock_st_cls):
        """provider=sentence_transformers creates SentenceTransformersEmbedding."""
        settings = EmbeddingSettings(
            provider="sentence_transformers",
            model="BAAI/bge-small-zh-v1.5",
            dimensions=512,
        )
        from src.libs.embedding.embedding_factory import EmbeddingFactory
        emb = EmbeddingFactory.create(settings)
        assert emb.__class__.__name__ == "SentenceTransformersEmbedding"

    @patch("src.libs.embedding.huggingface_embedding.AutoModel")
    @patch("src.libs.embedding.huggingface_embedding.AutoTokenizer")
    @patch("src.libs.embedding.huggingface_embedding.torch")
    def test_factory_routes_to_huggingface(self, mock_torch, mock_tokenizer_cls, mock_model_cls):
        """provider=huggingface creates HuggingFaceEmbedding."""
        settings = EmbeddingSettings(
            provider="huggingface",
            model="BAAI/bge-small-zh-v1.5",
            dimensions=512,
        )
        from src.libs.embedding.embedding_factory import EmbeddingFactory
        emb = EmbeddingFactory.create(settings)
        assert emb.__class__.__name__ == "HuggingFaceEmbedding"


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Test error handling for local embedding providers."""

    @patch("src.libs.embedding.sentence_transformers_embedding.SentenceTransformer", None)
    def test_sentence_transformers_not_installed(self):
        """Raises error when sentence-transformers not installed."""
        settings = EmbeddingSettings(
            provider="sentence_transformers",
            model="BAAI/bge-small-zh-v1.5",
            dimensions=512,
        )
        from src.libs.embedding.sentence_transformers_embedding import SentenceTransformersEmbedding

        with pytest.raises(EmbeddingError) as exc_info:
            SentenceTransformersEmbedding(settings)
        assert "not installed" in str(exc_info.value)
