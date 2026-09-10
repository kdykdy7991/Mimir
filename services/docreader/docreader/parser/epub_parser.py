# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #6: EPUB).
# Dependency-free: EPUB is a ZIP of (X)HTML documents; we strip each to text and
# concatenate in filename order. No third-party epub reader.
"""EPUB -> text parser (stdlib zipfile + html stripping)."""

from __future__ import annotations

import io
import logging
import zipfile

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.html_parser import _html_to_text

logger = logging.getLogger(__name__)


class EpubParser(BaseParser):
    """Parse .epub bytes: concatenate all (X)HTML parts' readable text."""

    def parse_into_text(self, content: bytes) -> Document:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = zf.namelist()
                parts = sorted(n for n in names if n.lower().endswith((".xhtml", ".html", ".htm")))
                if not parts and "META-INF/container.xml" not in names:
                    raise ValueError("epub has no content parts")
                blocks = []
                for n in parts:
                    text = _html_to_text(zf.read(n).decode("utf-8", errors="replace"))
                    if text.strip():
                        blocks.append(text)
        except zipfile.BadZipFile as exc:
            raise ValueError(f"invalid epub archive: {exc}") from exc
        return Document(content="\n\n".join(blocks).strip(),
                        metadata={"format": "epub", "parser": "builtin"})