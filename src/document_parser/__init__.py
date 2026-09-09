"""Unified local document parser (main-service side).

This package holds the project's document-parser contract and adapters that
front the standalone DocReader service (plan §7 target directory
``src/document_parser/``):

- ``types.py``  — ``ParseRequest`` / ``ParsedDocument`` / ``ParsedImage`` (plan §5)
- ``errors.py`` — stable error taxonomy
- ``base.py``   — ``DocumentParser`` protocol
- ``client.py`` — DocReader gRPC/streaming client
- ``adapters.py`` — ``LegacyLoaderParserAdapter`` + ``ParsedDocumentAdapter``
- ``image_persistence.py`` — image save + Markdown ref-rewrite helper
- ``table_normalizer.py`` — Markdown/HTML table normalisation (Phase 3/4)
"""

__all__ = [
    "ParseRequest",
    "ParsedDocument",
    "ParsedImage",
    "ParseStatus",
    "ParseError",
    "UnsupportedFormatError",
    "EngineUnavailableError",
    "ParseTimeoutError",
    "ParseFailedError",
    "ImagePersistenceError",
    "DocumentParser",
    "ParserEngineInfo",
    "LegacyLoaderParserAdapter",
    "ParsedDocumentAdapter",
]

from src.document_parser.adapters import (  # noqa: E402
    LegacyLoaderParserAdapter,
    ParsedDocumentAdapter,
)
from src.document_parser.base import (  # noqa: E402
    DocumentParser,
    ParserEngineInfo,
)
from src.document_parser.errors import (  # noqa: E402
    EngineUnavailableError,
    ImagePersistenceError,
    ParseError,
    ParseFailedError,
    ParseTimeoutError,
    UnsupportedFormatError,
)
from src.document_parser.types import (  # noqa: E402
    ParseRequest,
    ParseStatus,
    ParsedDocument,
    ParsedImage,
)

__version__ = "0.1.0"