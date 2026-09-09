"""BaseLoader-compatible bridge from a :class:`DocumentParser`.

The ingestion pipeline (and every entry point) holds a single ``BaseLoader``
slot. This adapter lets a unified :class:`DocumentParser` (legacy adapter or
DocReader client) drop into that slot unchanged — ``load(path)`` reads the file
bytes, routes them through the parser, and maps the result back onto
:class:`core.types.Document` via :class:`ParsedDocumentAdapter`.

The pipeline code itself does not need to change; only the loader instance is
selected by the ``document_parser.backend`` feature flag (default ``legacy``).
"""

from __future__ import annotations

from pathlib import Path

from src.core.types import Document
from src.document_parser.adapters import ParsedDocumentAdapter
from src.document_parser.base import DocumentParser
from src.document_parser.types import ParseRequest
from src.libs.loader.base_loader import BaseLoader, LoaderError


class DocumentParserLoader(BaseLoader):
    """Bridge a :class:`DocumentParser` to the legacy :class:`BaseLoader` API."""

    def __init__(
        self,
        parser: DocumentParser,
        *,
        adapter: ParsedDocumentAdapter | None = None,
    ) -> None:
        self._parser = parser
        self._adapter = adapter or ParsedDocumentAdapter()

    def load(self, path: str) -> Document:
        file_path = self._require_file(path)
        try:
            content = file_path.read_bytes()
        except OSError as exc:
            raise LoaderError(f"Failed to read {path}: {exc}") from exc

        request = ParseRequest(
            source_path=str(file_path),
            file_name=file_path.name,
            file_type=file_path.suffix.lstrip(".").lower(),
            content=content,
        )
        try:
            parsed = self._parser.parse(request)
        except Exception as exc:  # noqa: BLE001 — surface as LoaderError
            raise LoaderError(str(exc)) from exc
        return self._adapter.to_document(parsed)

    def supported_extensions(self) -> frozenset[str]:
        return frozenset(
            info
            for engine in self._parser.list_engines()
            for info in engine.supported_formats
            if info.startswith(".")
        )

    def engine_summary(self) -> list[str]:
        return [e.name for e in self._parser.list_engines()]

    @staticmethod
    def _normalize_ext(ext: str) -> str:
        return ext if ext.startswith(".") else f".{ext}"