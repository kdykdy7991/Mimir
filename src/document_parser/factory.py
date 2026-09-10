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


def build_document_parser_from_settings(
    settings: DocumentParserSettings,
    *,
    loader: BaseLoader | None = None,
    grpc_transport=None,
) -> DocumentParser:
    """Build the parser selected by ``settings.backend`` end-to-end.

    For ``docreader`` this proves out the wire path: constructs a
    :class:`DocReaderGrpcTransport` against ``settings.endpoint`` and wraps it in
    :class:`DocReaderClient`. For ``legacy`` it wraps ``loader`` (the legacy
    LoaderRegistry chain), keeping the old path reachable. ``grpc_transport`` may
    be injected (tests / alternate transport).
    """
    if not settings.enabled:
        return _legacy(loader)
    if settings.backend == "docreader":
        transport = grpc_transport or _grpc_from(settings)
        client = DocReaderClient(transport, timeout=float(settings.request_timeout_seconds))
        return DocReaderClientParser(client)
    return _legacy(loader)


def _grpc_from(settings: DocumentParserSettings):
    from src.document_parser.grpc_transport import DocReaderGrpcTransport
    return DocReaderGrpcTransport(settings.endpoint, timeout=float(settings.request_timeout_seconds))


def resolve_document_parser(
    settings: DocumentParserSettings,
    *,
    loader: BaseLoader | None = None,
    grpc_transport=None,
) -> DocumentParser | None:
    """Return the parser the ingestion entry should bridge into the pipeline.

    - ``backend == "docreader"`` and enabled: build the DocReader parser.
      A build/transport failure RAISES here (fail-fast at startup / pipeline
      construction) rather than silently substituting the legacy chain — a
      misconfigured docreader backend must never quietly parse through the old
      loaders.
    - otherwise (``legacy`` explicit, disabled, or default-safe code default):
      return None so the caller keeps the legacy ``LoaderRegistry`` path.
    """
    if not settings.enabled or settings.backend != "docreader":
        return None
    return build_document_parser_from_settings(
        settings, loader=loader, grpc_transport=grpc_transport,
    )