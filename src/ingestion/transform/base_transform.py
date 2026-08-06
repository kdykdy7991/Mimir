"""
Transform abstract base class.

A Transform is a stage in the ingestion pipeline that takes a list
of :class:`core.types.Chunk` and returns a new list of Chunks. The
contract:

- Input chunks are never mutated — Transforms return *new* Chunk
  objects (or, for no-op stages, the same list).
- Each Transform receives an optional :class:`core.trace.TraceContext`
  to record per-stage events for observability.
- A failure inside one chunk MUST NOT abort the whole batch — each
  Transform implementation is responsible for per-chunk isolation
  and graceful degradation (the ChunkRefiner demonstrates this).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.core.types import Chunk

if TYPE_CHECKING:
    from src.core.trace.trace_context import TraceContext


class TransformError(Exception):
    """Base exception for Transform-related failures."""


class BaseTransform(ABC):
    """
    Abstract base class for ingestion transforms.

    Subclasses implement ``transform`` to do the actual work
    (refining text, enriching metadata, computing embeddings, …).
    """

    name: str = "base_transform"  # subclass override; used for trace stages

    @abstractmethod
    def transform(
        self,
        chunks: list[Chunk],
        trace: "TraceContext | None" = None,
    ) -> list[Chunk]:
        """
        Apply the transform to ``chunks``.

        Args:
            chunks: Input chunks. Implementations MUST NOT mutate
                the input Chunk objects in place — return new ones.
            trace: Optional trace context. Implementations SHOULD
                call ``trace.record_stage(self.name, ...)`` at least
                once (typically at start + finish) to support
                observability.

        Returns:
            list[Chunk]: Transformed chunks. May be the same length
            as the input, or shorter if a filter was applied.
        """
        pass
