"""
Smoke tests for Embedding providers (OpenAI, Azure).

Tests use mock HTTP to avoid real network calls.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.core.settings import EmbeddingSettings
from src.libs.embedding.base_embedding import BaseEmbedding, EmbeddingError
from src.libs.embedding.embedding_factory import EmbeddingFactory


# ---------------------------------------------------------------------------
# Mock response helpers
# ---------------------------------------------------------------------------

def mock_embedding_response(dimensions: int = 3) -> MagicMock:
    """Create a mock embedding response."""
    mock_data = MagicMock()
    mock_data.index = 0
    mock_data.embedding = [0.1] * dimensions

    mock_response = MagicMock()
    mock_response.data = [mock_data]
    return mock_response


def mock_batch_embedding_response(count: int = 2, dimensions: int = 3) -> MagicMock:
    """Create a mock batch embedding response."""
    mock_data_list = []
    for i in range(count):
        mock_data = MagicMock()
        mock_data.index = i
        mock_data.embedding = [0.1 * (i + 1)] * dimensions
        mock_data_list.append(mock_data)

    mock_response = MagicMock()
    mock_response.data = mock_data_list
    return mock_response


def _stub_client_with_one_model(
    mock_openai_cls, model_id: str = "discovered-model",
) -> MagicMock:
    """Create a mock OpenAI client that auto-discovers a single
    model id from ``/v1/models``. Used by every test that goes
    through ``OpenAIEmbedding.__init__`` (the new discovery call
    would otherwise raise).

    The returned client can be further customized by the caller
    (e.g. setting ``mock_client.embeddings.create.return_value``).
    """
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.models.list.return_value.data = [MagicMock(id=model_id)]
    return mock_client


# ---------------------------------------------------------------------------
# Tests: Factory routing
# ---------------------------------------------------------------------------

class TestProviderRouting:
    """Test that factory routes to correct provider class."""

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_factory_routes_to_openai(self, mock_openai_cls):
        """provider=openai creates OpenAIEmbedding."""
        _stub_client_with_one_model(mock_openai_cls, model_id="fake-model")

        settings = EmbeddingSettings(
            provider="openai", api_key="test-key", model="fake-model",
        )
        emb = EmbeddingFactory.create(settings)
        assert emb.__class__.__name__ == "OpenAIEmbedding"


# ---------------------------------------------------------------------------
# Tests: OpenAI Embedding
# ---------------------------------------------------------------------------

class TestOpenAIEmbedding:
    """Test OpenAI Embedding provider with mock."""

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_embed_returns_vectors(self, mock_openai_cls):
        """embed() returns list of vectors."""
        mock_client = _stub_client_with_one_model(mock_openai_cls)
        mock_client.embeddings.create.return_value = mock_embedding_response(dimensions=3)

        settings = EmbeddingSettings(
            provider="openai", api_key="test-key",
            model="text-embedding-3-small", dimensions=3
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        emb = OpenAIEmbedding(settings)

        result = emb.embed(["hello world"])
        assert isinstance(result, list)
        assert len(result) == 1
        assert len(result[0]) == 3

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_embed_batch(self, mock_openai_cls):
        """embed() handles batch input."""
        mock_client = _stub_client_with_one_model(mock_openai_cls)
        mock_client.embeddings.create.return_value = mock_batch_embedding_response(count=3)

        settings = EmbeddingSettings(
            provider="openai", api_key="test-key",
            model="text-embedding-3-small", dimensions=3
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        emb = OpenAIEmbedding(settings)

        result = emb.embed(["hello", "world", "test"])
        assert len(result) == 3

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_embed_empty_list(self, mock_openai_cls):
        """embed() handles empty input."""
        _stub_client_with_one_model(mock_openai_cls)

        settings = EmbeddingSettings(provider="openai", api_key="test-key", model="x")
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        emb = OpenAIEmbedding(settings)

        result = emb.embed([])
        assert result == []
        # Should not call API
        mock_openai_cls.return_value.embeddings.create.assert_not_called()

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_dimensions_property(self, mock_openai_cls):
        """dimensions property returns correct value."""
        _stub_client_with_one_model(mock_openai_cls)

        settings = EmbeddingSettings(
            provider="openai", api_key="test-key", dimensions=1536, model="x"
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        emb = OpenAIEmbedding(settings)

        assert emb.dimensions == 1536

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_uses_custom_base_url(self, mock_openai_cls):
        """Uses custom base_url from settings."""
        _stub_client_with_one_model(mock_openai_cls)

        settings = EmbeddingSettings(
            provider="openai", api_key="test-key",
            base_url="https://custom.api.com/v1", model="x"
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        OpenAIEmbedding(settings)

        call_kwargs = mock_openai_cls.call_args.kwargs
        assert call_kwargs["base_url"] == "https://custom.api.com/v1"


# ---------------------------------------------------------------------------
# Tests: Model auto-discovery
# ---------------------------------------------------------------------------

class TestOpenAIEmbeddingModelDiscovery:
    """Tests for ``OpenAIEmbedding._discover_model`` decision tree."""

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_init_discovers_single_model(self, mock_openai_cls):
        """API returns 1 model -> instance uses that id."""
        _stub_client_with_one_model(
            mock_openai_cls, model_id="qwen3-embedding",
        )
        settings = EmbeddingSettings(
            provider="openai", api_key="k",
            model="text-embedding-3-small", dimensions=3,
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        emb = OpenAIEmbedding(settings)

        assert emb.model == "qwen3-embedding"
        # settings is mutated so the dashboard sees the live id.
        assert emb.settings.model == "qwen3-embedding"

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_init_uses_configured_model_when_in_list(self, mock_openai_cls, caplog):
        """API returns N>1, configured in list -> use configured, no warning."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.return_value.data = [
            MagicMock(id="qwen3-embedding"),
            MagicMock(id="bge-large"),
        ]

        settings = EmbeddingSettings(
            provider="openai", api_key="k", model="bge-large", dimensions=3,
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        with caplog.at_level(logging.WARNING):
            emb = OpenAIEmbedding(settings)

        assert emb.model == "bge-large"
        # No "not among them" warning when configured matches.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("not among them" in r.message for r in warnings)

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_init_uses_first_when_configured_not_in_list(self, mock_openai_cls, caplog):
        """API returns N>1, configured NOT in list -> use first, warn."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.return_value.data = [
            MagicMock(id="alpha"), MagicMock(id="beta"),
        ]

        settings = EmbeddingSettings(
            provider="openai", api_key="k",
            model="text-embedding-3-small", dimensions=3,
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        with caplog.at_level(logging.WARNING):
            emb = OpenAIEmbedding(settings)

        assert emb.model == "alpha"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("not among them" in r.message for r in warnings), (
            f"expected a 'not among them' warning, got: "
            f"{[r.message for r in caplog.records]}"
        )

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_init_falls_back_when_api_empty(self, mock_openai_cls, caplog):
        """API returns 0 models -> fall back to settings.model, warn."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.return_value.data = []

        settings = EmbeddingSettings(
            provider="openai", api_key="k",
            model="text-embedding-3-small", dimensions=3,
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        with caplog.at_level(logging.WARNING):
            emb = OpenAIEmbedding(settings)

        assert emb.model == "text-embedding-3-small"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("returned no models" in r.message for r in warnings)

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_init_raises_connection_error_on_api_failure(self, mock_openai_cls):
        """API raises -> EmbeddingConnectionError at __init__."""
        from src.libs.embedding.base_embedding import EmbeddingConnectionError

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.side_effect = Exception("Connection refused")

        settings = EmbeddingSettings(
            provider="openai", api_key="k", model="x", dimensions=3,
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        with pytest.raises(EmbeddingConnectionError) as excinfo:
            OpenAIEmbedding(settings)
        # The underlying error message is preserved in the chained exception.
        assert "Connection refused" in str(excinfo.value)

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_init_raises_on_timeout(self, mock_openai_cls):
        """API timeout -> EmbeddingConnectionError at __init__."""
        from src.libs.embedding.base_embedding import EmbeddingConnectionError

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.side_effect = Exception("timed out")

        settings = EmbeddingSettings(
            provider="openai", api_key="k", model="x", dimensions=3,
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        with pytest.raises(EmbeddingConnectionError):
            OpenAIEmbedding(settings)

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_discovery_call_uses_short_timeout(self, mock_openai_cls):
        """models.list() is called with the short discovery timeout."""
        from src.libs.embedding.openai_embedding import DISCOVERY_TIMEOUT

        _stub_client_with_one_model(mock_openai_cls)

        settings = EmbeddingSettings(
            provider="openai", api_key="k", model="x", dimensions=3,
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        OpenAIEmbedding(settings)

        mock_client = mock_openai_cls.return_value
        mock_client.models.list.assert_called_once_with(
            timeout=DISCOVERY_TIMEOUT,
        )


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Test error handling across providers."""

    @patch("src.libs.embedding.openai_embedding.OpenAI")
    def test_connection_error(self, mock_openai_cls):
        """Connection error is properly wrapped."""
        from src.libs.embedding.base_embedding import EmbeddingConnectionError

        mock_client = _stub_client_with_one_model(mock_openai_cls)
        mock_client.embeddings.create.side_effect = Exception("Connection timeout")

        settings = EmbeddingSettings(
            provider="openai", api_key="test-key", model="x",
        )
        from src.libs.embedding.openai_embedding import OpenAIEmbedding
        emb = OpenAIEmbedding(settings)

        with pytest.raises(EmbeddingConnectionError):
            emb.embed(["hello"])
