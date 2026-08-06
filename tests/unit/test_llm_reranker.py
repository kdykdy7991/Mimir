"""
Smoke tests for LLM Reranker implementation.

Tests use mock LLM client to avoid real API calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

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

def mock_llm_client(response: str = None):
    """Create a mock LLM client."""
    client = MagicMock()
    if response is None:
        # Default response: rank doc2 first, then doc1
        response = '''[
            {"id": "doc2", "relevance_score": 0.9},
            {"id": "doc1", "relevance_score": 0.7}
        ]'''
    client.chat.return_value = response
    return client


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

    def test_factory_routes_to_llm(self):
        """provider=llm creates LLMReranker."""
        settings = RerankSettings(backend="llm")
        reranker = RerankerFactory.create(settings)
        assert reranker.__class__.__name__ == "LLMReranker"


# ---------------------------------------------------------------------------
# Tests: LLM Reranker
# ---------------------------------------------------------------------------

class TestLLMReranker:
    """Test LLM Reranker with mock LLM client."""

    def test_rerank_returns_candidates(self):
        """rerank() returns list of RerankCandidate."""
        settings = RerankSettings(backend="llm")
        llm_client = mock_llm_client()
        from src.libs.reranker.llm_reranker import LLMReranker
        reranker = LLMReranker(settings, llm_client=llm_client)

        result = reranker.rerank("What is Python?", sample_candidates())
        assert isinstance(result, list)
        assert all(isinstance(c, RerankCandidate) for c in result)

    def test_rerank_reorders_by_llm(self):
        """rerank() reorders based on LLM response."""
        settings = RerankSettings(backend="llm")
        # LLM says doc2 is most relevant
        llm_client = mock_llm_client('''
            [
                {"id": "doc2", "relevance_score": 0.95},
                {"id": "doc1", "relevance_score": 0.7}
            ]
        ''')
        from src.libs.reranker.llm_reranker import LLMReranker
        reranker = LLMReranker(settings, llm_client=llm_client)

        result = reranker.rerank("data science", sample_candidates())
        assert result[0].id == "doc2"
        assert result[0].score == 0.95

    def test_rerank_respects_top_k(self):
        """rerank() respects top_k limit."""
        settings = RerankSettings(backend="llm")
        llm_client = mock_llm_client()
        from src.libs.reranker.llm_reranker import LLMReranker
        reranker = LLMReranker(settings, llm_client=llm_client)

        result = reranker.rerank("query", sample_candidates(), top_k=2)
        assert len(result) == 2

    def test_rerank_empty_candidates(self):
        """rerank() handles empty input."""
        settings = RerankSettings(backend="llm")
        llm_client = mock_llm_client()
        from src.libs.reranker.llm_reranker import LLMReranker
        reranker = LLMReranker(settings, llm_client=llm_client)

        result = reranker.rerank("query", [])
        assert result == []

    def test_rerank_no_llm_client_raises_error(self):
        """rerank() raises error when no LLM client."""
        settings = RerankSettings(backend="llm")
        from src.libs.reranker.llm_reranker import LLMReranker
        reranker = LLMReranker(settings, llm_client=None)

        with pytest.raises(RerankerError) as exc_info:
            reranker.rerank("query", sample_candidates())
        assert "not provided" in str(exc_info.value)

    def test_rerank_handles_invalid_json(self):
        """rerank() handles invalid LLM response gracefully."""
        settings = RerankSettings(backend="llm")
        llm_client = mock_llm_client("This is not JSON")
        from src.libs.reranker.llm_reranker import LLMReranker
        reranker = LLMReranker(settings, llm_client=llm_client)

        # Should return original order when parsing fails
        result = reranker.rerank("query", sample_candidates())
        assert len(result) == 3

    def test_rerank_includes_unranked_candidates(self):
        """rerank() includes candidates not ranked by LLM at the end."""
        settings = RerankSettings(backend="llm")
        # LLM only ranks doc1, not doc2 and doc3
        llm_client = mock_llm_client('''
            [{"id": "doc1", "relevance_score": 0.9}]
        ''')
        from src.libs.reranker.llm_reranker import LLMReranker
        reranker = LLMReranker(settings, llm_client=llm_client)

        result = reranker.rerank("query", sample_candidates())
        assert result[0].id == "doc1"
        assert len(result) == 3  # All candidates included


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Test error handling for LLM Reranker."""

    def test_llm_error_wrapped(self):
        """LLM errors are wrapped in RerankerError."""
        settings = RerankSettings(backend="llm")
        llm_client = mock_llm_client()
        llm_client.chat.side_effect = Exception("LLM API error")

        from src.libs.reranker.llm_reranker import LLMReranker
        reranker = LLMReranker(settings, llm_client=llm_client)

        with pytest.raises(RerankerError) as exc_info:
            reranker.rerank("query", sample_candidates())
        assert "LLM reranking failed" in str(exc_info.value)
