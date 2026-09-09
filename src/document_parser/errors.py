"""Document-parser error taxonomy (project-owned, not upstream-coupled).

Ported parsers raise engine/format-specific errors; this module gives the main
service a stable, typed surface to classify failures (plan §12.2: attempt
chains, partial/failed semantics, retries) without depending on the DocReader
service's internal exceptions.
"""

from __future__ import annotations


class ParseError(Exception):
    """Base class for all document-parser failures."""


class UnsupportedFormatError(ParseError):
    """File type is not supported by the selected parser/registry."""


class EngineUnavailableError(ParseError):
    """The requested parser engine is not available (not installed / disabled)."""


class ParseTimeoutError(ParseError):
    """A parse attempt exceeded its allowed time budget."""


class ParseFailedError(ParseError):
    """A parser engine reached a terminal failure for this payload.

    Optionally carries the ordered engines already tried so callers can report
    the full attempt chain (plan §6: ChainParser must NOT swallow the final
    error and must return the complete attempt record).
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts or []


class ImagePersistenceError(ParseError):
    """A parsed image could not be persisted / rewritten by the main service."""