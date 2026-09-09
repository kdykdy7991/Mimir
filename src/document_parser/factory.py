"""Build the active :class:`DocumentParser` from settings (feature flag).

Central resolution point for ``document_parser.backend``:
``legacy`` returns :class:`LegacyLoaderParserAdapter` (current stable path);
``docreader`` returns :class:`DocReaderClientParser` around a
:class:`DocReaderClient`. Requesting ``docreader`` without a transport raises
a clear :class:`EngineUnavailableError`, so misconfiguration surfaces at
startup, not mid-parse.
"""

from __future__ import annotations

from src.core.settings import DocumentParserSettings
from src.document_parser.base import DocumentParser
from src.document_parser.client import DocReaderClient, DocReaderTransport
from src.document_parser.docreader_parser import DocReaderClientParser
from src.document_parser.errors import EngineUnavailableError
from src.document_parser.adapters import LegacyLoaderParserAdapter
from src.libs.loader.base_loader import BaseLoader


def build_document_parser(
    settings: DocumentParserSettings,
    *,
    loader: BaseLoader | None = None,
    transport: DocReaderTransport | None = None,
) -> DocumentParser:
    """Return the parser selected by ``settings.backend``.

    - ``legacy``: wrap ``loader`` (the existing Loader chain).
    - ``docreader``: wrap a ``DocReaderClient``; requires a ``transport``.
    """
    if not settings.enabled:
        # Disabled -> fall back to the legacy chain for a stable run.
        return _legacy(loader)

    if settings.backend == "legacy":
        return _legacy(loader)

    if settings.backend == "docreader":
        if transport is None:
            raise EngineUnavailableError(
                "document_parser.backend='docreader' requires a configured "
                "DocReader transport (gRPC stubs / service not wired yet).",
            )
        client = DocReaderClient(
            transport, timeout=float(settings.request_timeout_seconds),
        )
        return DocReaderClientParser(client)

    raise EngineUnavailableError(
        f"Unknown document_parser.backend {settings.backend!r}",
    )


def _legacy(loader: BaseLoader | None) -> DocumentParser:
    if loader is None:
        raise EngineUnavailableError(
            "document_parser.backend='legacy' requires a BaseLoader; none was provided.",
        )
    return LegacyLoaderParserAdapter(loader)