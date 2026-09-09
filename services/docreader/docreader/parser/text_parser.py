# SPDX-License-Identifier: MIT
#
# Project-local plain-text/Markdown passthrough parser. Phase 1 makes the
# DocReader service runnable for txt/md so ReadStream/ListEngines can be
# exercised end-to-end and health-probed. Format parsers (PDF/DOCX/XLSX/...)
# land in later phases.
from __future__ import annotations

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser


class PlainTextParser(BaseParser):
    """Pass-through for plain text / Markdown (no conversion, no image sidecar)."""

    def parse_into_text(self, content: bytes) -> Document:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")
        return Document(content=text, metadata={"engine": self.__class__.__name__})