"""DocReader-backed :class:`DocumentParser` (selected by feature flag)."""

from __future__ import annotations

from src.document_parser.base import DocumentParser, ParserEngineInfo
from src.document_parser.client import DocReaderClient
from src.document_parser.types import ParseRequest, ParsedDocument


class DocReaderClientParser(DocumentParser):
    """A :class:`DocumentParser` that fronts the standalone DocReader service."""

    def __init__(self, client: DocReaderClient, *, engine_name: str = "docreader") -> None:
        self._client = client
        self._engine_name = engine_name

    def parse(self, request: ParseRequest) -> ParsedDocument:
        return self._client.parse(request)

    def list_engines(self) -> list[ParserEngineInfo]:
        return self._client.list_engines()

    def close(self) -> None:
        self._client.close()