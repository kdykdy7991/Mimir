"""
Smoke tests for Recursive Splitter implementation.

Tests use mock to avoid loading real LangChain dependencies.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.settings import SplitterSettings
from src.libs.splitter.base_splitter import BaseSplitter, SplitterError
from src.libs.splitter.splitter_factory import SplitterFactory


# ---------------------------------------------------------------------------
# Mock helper
# ---------------------------------------------------------------------------

def mock_recursive_splitter():
    """Create a mock RecursiveCharacterTextSplitter."""
    mock_splitter = MagicMock()
    # Simulate splitting behavior
    mock_splitter.split_text.side_effect = lambda text: text.split("\n\n")
    return mock_splitter


# ---------------------------------------------------------------------------
# Tests: Factory routing
# ---------------------------------------------------------------------------

class TestProviderRouting:
    """Test that factory routes to correct provider class."""

    @patch("src.libs.splitter.recursive_splitter.RecursiveCharacterTextSplitter")
    def test_factory_routes_to_recursive(self, mock_rcts_cls):
        """provider=recursive creates RecursiveSplitter."""
        settings = SplitterSettings(type="recursive", chunk_size=1000)
        from src.libs.splitter.splitter_factory import SplitterFactory
        splitter = SplitterFactory.create(settings)
        assert splitter.__class__.__name__ == "RecursiveSplitter"


# ---------------------------------------------------------------------------
# Tests: Recursive Splitter
# ---------------------------------------------------------------------------

class TestRecursiveSplitter:
    """Test Recursive Splitter with mock."""

    @patch("src.libs.splitter.recursive_splitter.RecursiveCharacterTextSplitter")
    def test_split_text_returns_chunks(self, mock_rcts_cls):
        """split_text() returns list of chunks."""
        mock_splitter = mock_recursive_splitter()
        mock_rcts_cls.return_value = mock_splitter

        settings = SplitterSettings(
            type="recursive",
            chunk_size=1000,
            chunk_overlap=200,
        )
        from src.libs.splitter.recursive_splitter import RecursiveSplitter
        splitter = RecursiveSplitter(settings)

        result = splitter.split_text("Paragraph 1\n\nParagraph 2\n\nParagraph 3")
        assert isinstance(result, list)
        assert len(result) == 3

    @patch("src.libs.splitter.recursive_splitter.RecursiveCharacterTextSplitter")
    def test_split_empty_text(self, mock_rcts_cls):
        """split_text() handles empty input."""
        settings = SplitterSettings(type="recursive", chunk_size=1000)
        from src.libs.splitter.recursive_splitter import RecursiveSplitter
        splitter = RecursiveSplitter(settings)

        result = splitter.split_text("")
        assert result == []
        # Should not call splitter
        mock_rcts_cls.return_value.split_text.assert_not_called()

    @patch("src.libs.splitter.recursive_splitter.RecursiveCharacterTextSplitter")
    def test_uses_settings_parameters(self, mock_rcts_cls):
        """Uses chunk_size and chunk_overlap from settings."""
        mock_splitter = mock_recursive_splitter()
        mock_rcts_cls.return_value = mock_splitter

        settings = SplitterSettings(
            type="recursive",
            chunk_size=500,
            chunk_overlap=100,
        )
        from src.libs.splitter.recursive_splitter import RecursiveSplitter
        RecursiveSplitter(settings)

        call_kwargs = mock_rcts_cls.call_args.kwargs
        assert call_kwargs["chunk_size"] == 500
        assert call_kwargs["chunk_overlap"] == 100

    @patch("src.libs.splitter.recursive_splitter.RecursiveCharacterTextSplitter")
    def test_uses_custom_separators(self, mock_rcts_cls):
        """Uses custom separators when provided."""
        mock_splitter = mock_recursive_splitter()
        mock_rcts_cls.return_value = mock_splitter

        settings = SplitterSettings(
            type="recursive",
            chunk_size=1000,
            separators=["---", "||", " "],
        )
        from src.libs.splitter.recursive_splitter import RecursiveSplitter
        RecursiveSplitter(settings)

        call_kwargs = mock_rcts_cls.call_args.kwargs
        assert call_kwargs["separators"] == ["---", "||", " "]

    @patch("src.libs.splitter.recursive_splitter.RecursiveCharacterTextSplitter")
    def test_markdown_splitting(self, mock_rcts_cls):
        """Handles Markdown structure."""
        def mock_split(text):
            # Simulate preserving code blocks
            if "```" in text:
                return [text]
            return text.split("\n\n")

        mock_splitter = MagicMock()
        mock_splitter.split_text.side_effect = mock_split
        mock_rcts_cls.return_value = mock_splitter

        settings = SplitterSettings(type="recursive", chunk_size=1000)
        from src.libs.splitter.recursive_splitter import RecursiveSplitter
        splitter = RecursiveSplitter(settings)

        markdown = "# Title\n\nParagraph\n\n```python\ncode\n```"
        result = splitter.split_text(markdown)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Test error handling for Recursive Splitter."""

    @patch("src.libs.splitter.recursive_splitter.RecursiveCharacterTextSplitter", None)
    def test_langchain_not_installed(self):
        """Raises error when langchain not installed."""
        settings = SplitterSettings(type="recursive", chunk_size=1000)
        from src.libs.splitter.recursive_splitter import RecursiveSplitter

        with pytest.raises(SplitterError) as exc_info:
            RecursiveSplitter(settings)
        assert "not installed" in str(exc_info.value)
