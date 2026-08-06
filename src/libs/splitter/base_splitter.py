"""
Splitter abstract base class.

Defines the unified interface for all text splitting strategies
(Recursive, Semantic, Fixed-length).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SplitChunk:
    """A single chunk produced by a splitter."""
    text: str
    index: int
    start_offset: int
    end_offset: int
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseSplitter(ABC):
    """
    Abstract base class for text splitters.

    All Splitter implementations must inherit from this class
    and implement the `split_text` method.
    """

    @abstractmethod
    def split_text(self, text: str, **kwargs: Any) -> list[str]:
        """
        Split text into chunks.

        Args:
            text: The text to split.
            **kwargs: Additional parameters (e.g., metadata for context).

        Returns:
            list[str]: List of text chunks.

        Raises:
            SplitterError: If splitting fails.
        """
        pass

    def split_into_chunks(self, text: str, **kwargs: Any) -> list[SplitChunk]:
        """
        Split text into structured chunks with metadata.

        Args:
            text: The text to split.
            **kwargs: Additional parameters.

        Returns:
            list[SplitChunk]: List of SplitChunk objects.
        """
        texts = self.split_text(text, **kwargs)
        chunks = []
        offset = 0
        for i, t in enumerate(texts):
            start = text.find(t, offset)
            if start == -1:
                start = offset
            end = start + len(t)
            chunks.append(SplitChunk(
                text=t,
                index=i,
                start_offset=start,
                end_offset=end,
            ))
            offset = end
        return chunks


class SplitterError(Exception):
    """Base exception for Splitter-related errors."""
    pass
