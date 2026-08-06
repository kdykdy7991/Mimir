"""
Unit tests for Embedding abstract interface and factory.

Tests cover:
- BaseEmbedding interface contract
- EmbeddingFactory routing logic
- Error handling for unsupported providers
"""

from __future__ import annotations

import pytest

from src.core.settings import EmbeddingSettings
from src.libs.embedding.base_embedding import BaseEmbedding, EmbeddingError
from src.libs.embedding.embedding_factory import EmbeddingFactory


# ---------------------------------------------------------------------------
# Fake Embedding implementations for testing
# ---------------------------------------------------------------------------

class FakeEmbedding(BaseEmbedding):
    """Fake Embedding for testing - returns fixed vectors."""

    def __init__(self, settings: EmbeddingSettings, dim: int = 8):
        self.settings = settings
        self._dimensions = dim
        self.call_count = 0
        self.last_texts = None

    def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        self.call_count += 1
        self.last_texts = texts
        # Return deterministic vectors based on text hash
        return [[float(hash(t) % 100) / 100] * self._dimensions for t in texts]

    @property
    def dimensions(self) -> int:
        return self._dimensions


class FakeEmbeddingSmall(BaseEmbedding):
    """Fake Embedding with different dimensions."""

    def __init__(self, settings: EmbeddingSettings):
        self.settings = settings

    def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    @property
    def dimensions(self) -> int:
        return 3


# ---------------------------------------------------------------------------
# Tests: BaseEmbedding interface contract
# ---------------------------------------------------------------------------

class TestBaseEmbeddingInterface:
    """Test that BaseEmbedding defines the correct interface."""

    def test_base_embedding_cannot_be_instantiated(self):
        """BaseEmbedding is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseEmbedding()

    def test_fake_embedding_satisfies_interface(self):
        """FakeEmbedding properly implements BaseEmbedding interface."""
        settings = EmbeddingSettings(provider="fake", model="fake-model")
        emb = FakeEmbedding(settings)

        assert isinstance(emb, BaseEmbedding)
        assert hasattr(emb, "embed")
        assert hasattr(emb, "dimensions")
        assert callable(emb.embed)

    def test_embed_returns_list_of_vectors(self):
        """embed() must return list of list[float]."""
        settings = EmbeddingSettings(provider="fake", model="fake-model")
        emb = FakeEmbedding(settings, dim=8)

        result = emb.embed(["hello", "world"])
        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(v, list) for v in result)
        assert all(len(v) == 8 for v in result)

    def test_embed_single(self):
        """embed_single() returns a single vector."""
        settings = EmbeddingSettings(provider="fake", model="fake-model")
        emb = FakeEmbedding(settings, dim=8)

        result = emb.embed_single("hello")
        assert isinstance(result, list)
        assert len(result) == 8

    def test_dimensions_property(self):
        """dimensions property returns correct value."""
        settings = EmbeddingSettings(provider="fake", model="fake-model")
        emb = FakeEmbedding(settings, dim=16)

        assert emb.dimensions == 16

    def test_embed_empty_list(self):
        """embed() handles empty input."""
        settings = EmbeddingSettings(provider="fake", model="fake-model")
        emb = FakeEmbedding(settings)

        result = emb.embed([])
        assert result == []


# ---------------------------------------------------------------------------
# Tests: EmbeddingFactory routing
# ---------------------------------------------------------------------------

class TestEmbeddingFactory:
    """Test EmbeddingFactory routing logic."""

    def test_list_providers(self):
        """list_providers() returns supported providers."""
        providers = EmbeddingFactory.list_providers()
        assert isinstance(providers, list)
        assert "openai" in providers
        assert "sentence_transformers" in providers
        assert "huggingface" in providers

    def test_register_custom_provider(self):
        """register_provider() adds a custom provider."""
        EmbeddingFactory.register_provider(
            "fake", "tests.unit.test_embedding_factory.FakeEmbedding"
        )

        providers = EmbeddingFactory.list_providers()
        assert "fake" in providers

        # Clean up
        from src.libs.embedding.embedding_factory import _PROVIDER_REGISTRY
        del _PROVIDER_REGISTRY["fake"]

    def test_create_with_fake_provider(self):
        """Factory can create instance with registered fake provider."""
        EmbeddingFactory.register_provider(
            "fake", "tests.unit.test_embedding_factory.FakeEmbedding"
        )

        try:
            settings = EmbeddingSettings(provider="fake", model="fake-model")
            emb = EmbeddingFactory.create(settings)

            assert emb.__class__.__name__ == "FakeEmbedding"
            assert emb.settings.provider == "fake"
            assert hasattr(emb, "embed")
        finally:
            from src.libs.embedding.embedding_factory import _PROVIDER_REGISTRY
            del _PROVIDER_REGISTRY["fake"]

    def test_unsupported_provider_raises_error(self):
        """Factory raises EmbeddingError for unsupported provider."""
        settings = EmbeddingSettings(provider="nonexistent", model="model")

        with pytest.raises(EmbeddingError) as exc_info:
            EmbeddingFactory.create(settings)

        assert "nonexistent" in str(exc_info.value)
        assert "Unsupported" in str(exc_info.value)

    def test_provider_case_insensitive(self):
        """Factory handles provider names case-insensitively."""
        EmbeddingFactory.register_provider(
            "fake", "tests.unit.test_embedding_factory.FakeEmbedding"
        )

        try:
            settings = EmbeddingSettings(provider="FAKE", model="model")
            emb = EmbeddingFactory.create(settings)
            assert emb.__class__.__name__ == "FakeEmbedding"
        finally:
            from src.libs.embedding.embedding_factory import _PROVIDER_REGISTRY
            del _PROVIDER_REGISTRY["fake"]


# ---------------------------------------------------------------------------
# Tests: Fake embedding stability
# ---------------------------------------------------------------------------

class TestFakeEmbeddingStability:
    """Test that Fake embedding returns stable vectors (验收标准)."""

    def test_same_input_same_output(self):
        """Same text always produces same vector."""
        settings = EmbeddingSettings(provider="fake", model="fake-model")
        emb = FakeEmbedding(settings, dim=8)

        result1 = emb.embed(["hello"])[0]
        result2 = emb.embed(["hello"])[0]
        assert result1 == result2

    def test_different_input_different_output(self):
        """Different texts produce different vectors."""
        settings = EmbeddingSettings(provider="fake", model="fake-model")
        emb = FakeEmbedding(settings, dim=8)

        result1 = emb.embed(["hello"])[0]
        result2 = emb.embed(["world"])[0]
        assert result1 != result2

    def test_batch_embed_count(self):
        """Batch embed returns correct number of vectors."""
        settings = EmbeddingSettings(provider="fake", model="fake-model")
        emb = FakeEmbedding(settings, dim=8)

        texts = ["a", "b", "c", "d", "e"]
        result = emb.embed(texts)
        assert len(result) == 5
