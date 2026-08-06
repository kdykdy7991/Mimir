"""
Smoke tests for Cross-Encoder Reranker implementation.

Tests use mock CrossEncoder to avoid loading real models.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.core.settings import RerankSettings
from src.libs.reranker.base_reranker import (
    BaseReranker,
    RerankCandidate,
    RerankerError,
)
from src.libs.reranker.reranker_factory import RerankerFactory


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def mock_cross_encoder(scores=None):
    """Create a mock CrossEncoder."""
    mock_model = MagicMock()
    if scores is None:
        # Default: doc2 most relevant, then doc1, then doc3
        scores = np.array([0.7, 0.9, 0.3])
    mock_model.predict.return_value = scores
    return mock_model


def sample_candidates():
    """Create sample candidates for testing."""
    return [
        RerankCandidate(id="doc1", text="Python is a programming language", score=0.5),
        RerankCandidate(id="doc2", text="Python is used for data science", score=0.6),
        RerankCandidate(id="doc3", text="Java is also a language", score=0.4),
    ]


# ---------------------------------------------------------------------------
# Tests: Factory routing
# ---------------------------------------------------------------------------

class TestProviderRouting:
    """Test that factory routes to correct provider class."""

    @patch("src.libs.reranker.cross_encoder_reranker.CrossEncoder")
    def test_factory_routes_to_cross_encoder(self, mock_ce_cls):
        """provider=cross_encoder creates CrossEncoderReranker."""
        settings = RerankSettings(backend="cross_encoder", model="test-model")
        from src.libs.reranker.reranker_factory import RerankerFactory
        reranker = RerankerFactory.create(settings)
        assert reranker.__class__.__name__ == "CrossEncoderReranker"


# ---------------------------------------------------------------------------
# Tests: Cross-Encoder Reranker
# ---------------------------------------------------------------------------

class TestCrossEncoderReranker:
    """Test Cross-Encoder Reranker with mock model."""

    @patch("src.libs.reranker.cross_encoder_reranker.CrossEncoder")
    def test_rerank_returns_candidates(self, mock_ce_cls):
        """rerank() returns list of RerankCandidate."""
        mock_model = mock_cross_encoder()
        mock_ce_cls.return_value = mock_model

        settings = RerankSettings(backend="cross_encoder", model="test-model")
        from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker(settings)

        result = reranker.rerank("What is Python?", sample_candidates())
        assert isinstance(result, list)
        assert all(isinstance(c, RerankCandidate) for c in result)

    @patch("src.libs.reranker.cross_encoder_reranker.CrossEncoder")
    def test_rerank_sorts_by_score(self, mock_ce_cls):
        """rerank() sorts candidates by score (descending)."""
        # doc2 has highest score (0.9), then doc1 (0.7), then doc3 (0.3)
        mock_model = mock_cross_encoder(np.array([0.7, 0.9, 0.3]))
        mock_ce_cls.return_value = mock_model

        settings = RerankSettings(backend="cross_encoder", model="test-model")
        from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker(settings)

        result = reranker.rerank("data science", sample_candidates())
        assert result[0].id == "doc2"
        assert result[0].score == 0.9
        assert result[1].id == "doc1"
        assert result[2].id == "doc3"

    @patch("src.libs.reranker.cross_encoder_reranker.CrossEncoder")
    def test_rerank_respects_top_k(self, mock_ce_cls):
        """rerank() respects top_k limit."""
        mock_model = mock_cross_encoder()
        mock_ce_cls.return_value = mock_model

        settings = RerankSettings(backend="cross_encoder", model="test-model")
        from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker(settings)

        result = reranker.rerank("query", sample_candidates(), top_k=2)
        assert len(result) == 2

    @patch("src.libs.reranker.cross_encoder_reranker.CrossEncoder")
    def test_rerank_empty_candidates(self, mock_ce_cls):
        """rerank() handles empty input."""
        settings = RerankSettings(backend="cross_encoder", model="test-model")
        from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker(settings)

        result = reranker.rerank("query", [])
        assert result == []
        # Should not call model.predict
        mock_ce_cls.return_value.predict.assert_not_called()

    @patch("src.libs.reranker.cross_encoder_reranker.CrossEncoder")
    def test_rerank_preserves_metadata(self, mock_ce_cls):
        """rerank() preserves metadata from original candidates."""
        mock_model = mock_cross_encoder(np.array([0.9, 0.5]))
        mock_ce_cls.return_value = mock_model

        candidates = [
            RerankCandidate(id="1", text="a", score=0.0, metadata={"source": "test"}),
            RerankCandidate(id="2", text="b", score=0.0),
        ]

        settings = RerankSettings(backend="cross_encoder", model="test-model")
        from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker(settings)

        result = reranker.rerank("query", candidates)
        assert result[0].metadata == {"source": "test"}

    @patch("src.libs.reranker.cross_encoder_reranker.CrossEncoder")
    def test_uses_device_from_settings(self, mock_ce_cls):
        """Uses device from settings."""
        settings = RerankSettings(
            backend="cross_encoder",
            model="test-model",
            device="cuda",
        )
        from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
        CrossEncoderReranker(settings)

        call_kwargs = mock_ce_cls.call_args.kwargs
        assert call_kwargs["device"] == "cuda"


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Test error handling for Cross-Encoder Reranker."""

    @patch("src.libs.reranker.cross_encoder_reranker.CrossEncoder", None)
    def test_sentence_transformers_not_installed(self):
        """Raises error when sentence-transformers not installed."""
        settings = RerankSettings(backend="cross_encoder", model="test-model")
        from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker

        with pytest.raises(RerankerError) as exc_info:
            CrossEncoderReranker(settings)
        assert "not installed" in str(exc_info.value)

    @patch("src.libs.reranker.cross_encoder_reranker.CrossEncoder")
    def test_model_error_wrapped(self, mock_ce_cls):
        """Model errors are wrapped in RerankerError."""
        mock_model = MagicMock()
        mock_model.predict.side_effect = Exception("Model loading failed")
        mock_ce_cls.return_value = mock_model

        settings = RerankSettings(backend="cross_encoder", model="test-model")
        from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker(settings)

        with pytest.raises(RerankerError) as exc_info:
            reranker.rerank("query", sample_candidates())
        assert "Cross-Encoder reranking failed" in str(exc_info.value)
