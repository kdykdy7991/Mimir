"""
Recursive Splitter implementation.

Uses LangChain's RecursiveCharacterTextSplitter for text splitting.
"""

from __future__ import annotations

from typing import Any

from src.libs.splitter.base_splitter import BaseSplitter, SplitterError

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    RecursiveCharacterTextSplitter = None  # type: ignore


class RecursiveSplitter(BaseSplitter):
    """
    Recursive Character Text Splitter.

    Uses LangChain's RecursiveCharacterTextSplitter which splits text
    recursively by different characters (paragraphs, sentences, words)
    to find good split points.

    Ideal for Markdown and structured text.
    """

    def __init__(self, settings: Any):
        if RecursiveCharacterTextSplitter is None:
            raise SplitterError(
                "langchain-text-splitters package is not installed. "
                "Install it with: pip install langchain-text-splitters"
            )
        self.settings = settings
        self._chunk_size = settings.chunk_size
        self._chunk_overlap = settings.chunk_overlap
        self._separators = getattr(settings, "separators", None)

        # Default separators for Markdown
        if self._separators is None:
            self._separators = ["\n\n", "\n", " ", ""]

        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            separators=self._separators,
            length_function=len,
        )

    def split_text(self, text: str, **kwargs: Any) -> list[str]:
        """
        Split text using recursive character splitting.

        Args:
            text: The text to split.
            **kwargs: Additional parameters.

        Returns:
            list[str]: List of text chunks.
        """
        if not text:
            return []

        try:
            return self._splitter.split_text(text)
        except Exception as e:
            raise SplitterError(f"Recursive splitting failed: {e}") from e
