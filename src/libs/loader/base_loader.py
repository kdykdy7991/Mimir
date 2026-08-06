"""
Loader abstract base class.

Defines the unified interface for turning a file on disk into a
``core.types.Document``. Implementations are responsible for:

- parsing the source format (PDF, DOCX, HTML, ...)
- extracting any embedded media (images, tables) the downstream
  pipeline may need
- producing a Document whose ``metadata`` honors the C1 contract
  (``source_path`` always set; ``images`` populated when applicable)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.core.types import Document


class LoaderError(Exception):
    """Base exception for loader failures."""


class BaseLoader(ABC):
    """
    Abstract base class for document loaders.

    A Loader is stateless with respect to a single file — it can be
    reused across many ``load()`` calls. Subclasses may carry config
    (e.g. image storage directory) in their constructor.
    """

    @abstractmethod
    def load(self, path: str) -> Document:
        """
        Load a single file and return a ``Document``.

        Args:
            path: Absolute or relative path to the file.

        Returns:
            Document: Parsed document. ``metadata.source_path`` MUST
            be set; ``metadata.images`` SHOULD be populated when the
            source format embeds images.

        Raises:
            LoaderError: On any unrecoverable parsing failure.
        """
        pass

    # ------------------------------------------------------------------
    # Convenience helpers — not abstract.
    # ------------------------------------------------------------------
    def load_many(self, paths: list[str]) -> list[Document]:
        """Load a batch of files; preserves input order.

        A failure in one file aborts the batch and propagates the
        exception. Callers that want best-effort behavior should call
        ``load()`` per file inside a try/except.
        """
        return [self.load(p) for p in paths]

    @staticmethod
    def _require_file(path: str) -> Path:
        """Resolve to a Path and ensure it exists / is a regular file."""
        p = Path(path)
        if not p.is_file():
            raise LoaderError(f"File not found or not a regular file: {path}")
        return p
