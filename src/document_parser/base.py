"""DocumentParser abstraction (plan §5.3).

A ``DocumentParser`` is the main-service view of "parse a file into a
``ParsedDocument``". Two implementations exist during the migration:

- :class:`LegacyLoaderParserAdapter` — wraps the existing Loader chain so the
  old PDF/Markdown path keeps working while DocReader lands (never removed
  wholesale; see ``adapters.py``).
- :class:`DocReaderClientParser` — talks to the standalone DocReader service
  (``client.py``) and is selected via the
  ``document_parser.backend: legacy | docreader`` feature flag.

Both outputs must travel through the same ``ParsedDocument -> Document``
adapter (plan §5.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from src.document_parser.types import ParseRequest, ParsedDocument


@dataclass(frozen=True)
class ParserEngineInfo:
    """Advertised capabilities of one parse engine.

    ``supported_formats`` lists extensions/MIME the engine claims; ``available``
    reflects runtime availability (e.g. engine not installed, service down), with
    ``unavailable_reason`` set when ``available`` is False. This powers the
    system endpoint ``GET /api/v1/parser-engines`` (plan §10).
    """

    name: str
    supported_formats: tuple[str, ...] = ()
    available: bool = True
    unavailable_reason: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class DocumentParser(Protocol):
    """Parse one document request -> structured result."""

    def parse(self, request: ParseRequest) -> ParsedDocument:
        """Parse ``request`` and return a ``ParsedDocument``.

        Raises a ``ParseError`` subclass on unrecoverable failure; engines may
        set ``ParsedDocument.parse_status`` to PARTIAL_SUCCESS when parts failed
        but the body is still usable.
        """
        ...

    def list_engines(self) -> list[ParserEngineInfo]:
        """Advertise available engines and their supported formats."""
        ...