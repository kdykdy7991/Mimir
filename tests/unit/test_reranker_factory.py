"""
Unit tests for Reranker abstract interface and factory.

Tests cover:
- BaseReranker interface contract
- NoneReranker behavior (preserves order)
- RerankerFactory routing logic
- Error handling for unsupported backends
"""

from __future__ import annotations

import pytest

from src.core.settings import RerankSettings
from src.libs.reranker.base_reranker import (
    BaseReranker,
    NoneReranker,
    RerankCandidate,
    RerankerError,
)
from src.libs.reranker.reranker_factory import RerankerFactory


# ---------------------------------------------------------------------------
# Fake Reranker for testing
# ---------------------------------------------------------------------------

class FakeReranker(BaseReranker):
    """Fake Reranker that reverses the order."""

    def __init__(self, settings: RerankSettings):
        self.settings = settings

    def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        top_k: int | None = None,
        **kwargs,
    ) -> list[RerankCandidate]:
        reversed_list = list(reversed(candidates))
        if top_k is not None:
            return reversed_list[:top_k]
        return reversed_list


# ---------------------------------------------------------------------------
# Tests: RerankCandidate dataclass
# ---------------------------------------------------------------------------

class TestRerankCandidate:
    """Test RerankCandidate dataclass."""

    def test_required_fields(self):
        """RerankCandidate requires id, text, score."""
        candidate = RerankCandidate(id="1", text="hello", score=0.9)
        assert candidate.id == "1"
        assert candidate.text == "hello"
        assert candidate.score == 0.9
        assert candidate.metadata == {}

    def test_optional_metadata(self):
        """RerankCandidate supports optional metadata."""
        candidate = RerankCandidate(
            id="1", text="hello", score=0.9,
            metadata={"source": "test.txt"}
        )
        assert candidate.metadata["source"] == "test.txt"


# ---------------------------------------------------------------------------
# Tests: NoneReranker
# ---------------------------------------------------------------------------

class TestNoneReranker:
    """Test NoneReranker preserves original order."""

    def test_preserves_order(self):
        """NoneReranker returns candidates in original order."""
        reranker = NoneReranker()
        candidates = [
            RerankCandidate(id="1", text="a", score=0.5),
            RerankCandidate(id="2", text="b", score=0.8),
            RerankCandidate(id="3", text="c", score=0.3),
        ]

        result = reranker.rerank("query", candidates)
        assert [c.id for c in result] == ["1", "2", "3"]

    def test_respects_top_k(self):
        """NoneReranker respects top_k limit."""
        reranker = NoneReranker()
        candidates = [
            RerankCandidate(id="1", text="a", score=0.5),
            RerankCandidate(id="2", text="b", score=0.8),
            RerankCandidate(id="3", text="c", score=0.3),
        ]

        result = reranker.rerank("query", candidates, top_k=2)
        assert len(result) == 2
        assert [c.id for c in result] == ["1", "2"]

    def test_empty_candidates(self):
        """NoneReranker handles empty input."""
        reranker = NoneReranker()
        result = reranker.rerank("query", [])
        assert result == []


# ---------------------------------------------------------------------------
# Tests: BaseReranker interface
# ---------------------------------------------------------------------------

class TestBaseRerankerInterface:
    """Test that BaseReranker defines the correct interface."""

    def test_base_cannot_be_instantiated(self):
        """BaseReranker is abstract."""
        with pytest.raises(TypeError):
            BaseReranker()

    def test_fake_satisfies_interface(self):
        """FakeReranker satisfies the interface."""
        settings = RerankSettings(backend="fake")
        reranker = FakeReranker(settings)

        assert isinstance(reranker, BaseReranker)
        assert hasattr(reranker, "rerank")
        assert callable(reranker.rerank)

    def test_rerank_reverses_order(self):
        """FakeReranker reverses candidate order."""
        settings = RerankSettings(backend="fake")
        reranker = FakeReranker(settings)

        candidates = [
            RerankCandidate(id="1", text="a", score=0.5),
            RerankCandidate(id="2", text="b", score=0.8),
        ]

        result = reranker.rerank("query", candidates)
        assert [c.id for c in result] == ["2", "1"]


# ---------------------------------------------------------------------------
# Tests: RerankerFactory routing
# ---------------------------------------------------------------------------

class TestRerankerFactory:
    """Test RerankerFactory routing logic."""

    def test_list_providers(self):
        """list_providers() returns supported backends."""
        providers = RerankerFactory.list_providers()
        assert isinstance(providers, list)
        assert "none" in providers
        assert "cross_encoder" in providers
        assert "llm" in providers

    def test_create_none_reranker(self):
        """Factory creates NoneReranker for backend='none'."""
        settings = RerankSettings(backend="none")
        reranker = RerankerFactory.create(settings)

        assert isinstance(reranker, NoneReranker)

    def test_unsupported_backend_raises_error(self):
        """Factory raises RerankerError for unsupported backend."""
        settings = RerankSettings(backend="nonexistent")

        with pytest.raises(RerankerError) as exc_info:
            RerankerFactory.create(settings)

        assert "nonexistent" in str(exc_info.value)
        assert "Unsupported" in str(exc_info.value)

    def test_backend_case_insensitive(self):
        """Factory handles backend names case-insensitively."""
        settings = RerankSettings(backend="NONE")
        reranker = RerankerFactory.create(settings)
        assert isinstance(reranker, NoneReranker)

    def test_register_custom_provider(self):
        """register_provider() adds a custom backend."""
        RerankerFactory.register_provider(
            "fake", "tests.unit.test_reranker_factory.FakeReranker"
        )

        try:
            providers = RerankerFactory.list_providers()
            assert "fake" in providers
        finally:
            from src.libs.reranker.reranker_factory import _PROVIDER_REGISTRY
            del _PROVIDER_REGISTRY["fake"]
