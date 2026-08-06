"""
Unit tests for Splitter abstract interface and factory.

Tests cover:
- BaseSplitter interface contract
- SplitterFactory routing logic
- Error handling for unsupported types
"""

from __future__ import annotations

import pytest

from src.core.settings import SplitterSettings
from src.libs.splitter.base_splitter import BaseSplitter, SplitChunk, SplitterError
from src.libs.splitter.splitter_factory import SplitterFactory


# ---------------------------------------------------------------------------
# Fake Splitter implementations for testing
# ---------------------------------------------------------------------------

class FakeSplitter(BaseSplitter):
    """Fake Splitter for testing - splits by fixed word count."""

    def __init__(self, settings: SplitterSettings, words_per_chunk: int = 5):
        self.settings = settings
        self.words_per_chunk = words_per_chunk

    def split_text(self, text: str, **kwargs) -> list[str]:
        words = text.split()
        chunks = []
        for i in range(0, len(words), self.words_per_chunk):
            chunk = " ".join(words[i:i + self.words_per_chunk])
            chunks.append(chunk)
        return chunks


class FakeSplitterByLine(BaseSplitter):
    """Fake Splitter that splits by newline."""

    def __init__(self, settings: SplitterSettings):
        self.settings = settings

    def split_text(self, text: str, **kwargs) -> list[str]:
        return [line for line in text.split("\n") if line.strip()]


# ---------------------------------------------------------------------------
# Tests: BaseSplitter interface contract
# ---------------------------------------------------------------------------

class TestBaseSplitterInterface:
    """Test that BaseSplitter defines the correct interface."""

    def test_base_splitter_cannot_be_instantiated(self):
        """BaseSplitter is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseSplitter()

    def test_fake_splitter_satisfies_interface(self):
        """FakeSplitter properly implements BaseSplitter interface."""
        settings = SplitterSettings(type="fake", chunk_size=100)
        splitter = FakeSplitter(settings)

        assert isinstance(splitter, BaseSplitter)
        assert hasattr(splitter, "split_text")
        assert callable(splitter.split_text)

    def test_split_text_returns_list_of_strings(self):
        """split_text() must return list[str]."""
        settings = SplitterSettings(type="fake", chunk_size=100)
        splitter = FakeSplitter(settings, words_per_chunk=2)

        result = splitter.split_text("a b c d e")
        assert isinstance(result, list)
        assert all(isinstance(c, str) for c in result)
        assert result == ["a b", "c d", "e"]

    def test_split_into_chunks(self):
        """split_into_chunks() returns SplitChunk objects."""
        settings = SplitterSettings(type="fake", chunk_size=100)
        splitter = FakeSplitter(settings, words_per_chunk=2)

        text = "hello world foo bar"
        chunks = splitter.split_into_chunks(text)

        assert isinstance(chunks, list)
        assert all(isinstance(c, SplitChunk) for c in chunks)
        assert len(chunks) == 2
        assert chunks[0].text == "hello world"
        assert chunks[0].index == 0
        assert chunks[0].start_offset == 0
        assert chunks[0].end_offset == 11

    def test_split_empty_text(self):
        """split_text() handles empty input."""
        settings = SplitterSettings(type="fake", chunk_size=100)
        splitter = FakeSplitter(settings)

        result = splitter.split_text("")
        assert result == []  # Empty input produces no chunks


# ---------------------------------------------------------------------------
# Tests: SplitterFactory routing
# ---------------------------------------------------------------------------

class TestSplitterFactory:
    """Test SplitterFactory routing logic."""

    def test_list_providers(self):
        """list_providers() returns supported types."""
        providers = SplitterFactory.list_providers()
        assert isinstance(providers, list)
        assert "recursive" in providers
        assert "semantic" in providers
        assert "fixed_length" in providers

    def test_register_custom_provider(self):
        """register_provider() adds a custom type."""
        SplitterFactory.register_provider(
            "fake", "tests.unit.test_splitter_factory.FakeSplitter"
        )

        providers = SplitterFactory.list_providers()
        assert "fake" in providers

        # Clean up
        from src.libs.splitter.splitter_factory import _PROVIDER_REGISTRY
        del _PROVIDER_REGISTRY["fake"]

    def test_create_with_fake_provider(self):
        """Factory can create instance with registered fake provider."""
        SplitterFactory.register_provider(
            "fake", "tests.unit.test_splitter_factory.FakeSplitter"
        )

        try:
            settings = SplitterSettings(type="fake", chunk_size=100)
            splitter = SplitterFactory.create(settings)

            assert splitter.__class__.__name__ == "FakeSplitter"
            assert hasattr(splitter, "split_text")
        finally:
            from src.libs.splitter.splitter_factory import _PROVIDER_REGISTRY
            del _PROVIDER_REGISTRY["fake"]

    def test_unsupported_type_raises_error(self):
        """Factory raises SplitterError for unsupported type."""
        settings = SplitterSettings(type="nonexistent", chunk_size=100)

        with pytest.raises(SplitterError) as exc_info:
            SplitterFactory.create(settings)

        assert "nonexistent" in str(exc_info.value)
        assert "Unsupported" in str(exc_info.value)

    def test_type_case_insensitive(self):
        """Factory handles type names case-insensitively."""
        SplitterFactory.register_provider(
            "fake", "tests.unit.test_splitter_factory.FakeSplitter"
        )

        try:
            settings = SplitterSettings(type="FAKE", chunk_size=100)
            splitter = SplitterFactory.create(settings)
            assert splitter.__class__.__name__ == "FakeSplitter"
        finally:
            from src.libs.splitter.splitter_factory import _PROVIDER_REGISTRY
            del _PROVIDER_REGISTRY["fake"]


# ---------------------------------------------------------------------------
# Tests: SplitChunk dataclass
# ---------------------------------------------------------------------------

class TestSplitChunk:
    """Test SplitChunk dataclass."""

    def test_basic_chunk(self):
        """Create a basic chunk with required fields."""
        chunk = SplitChunk(text="hello", index=0, start_offset=0, end_offset=5)
        assert chunk.text == "hello"
        assert chunk.index == 0
        assert chunk.start_offset == 0
        assert chunk.end_offset == 5
        assert chunk.metadata == {}

    def test_chunk_with_metadata(self):
        """Create a chunk with custom metadata."""
        chunk = SplitChunk(
            text="hello",
            index=0,
            start_offset=0,
            end_offset=5,
            metadata={"source": "test.txt", "page": 1},
        )
        assert chunk.metadata["source"] == "test.txt"
        assert chunk.metadata["page"] == 1
