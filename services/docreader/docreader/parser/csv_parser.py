# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #2: CSV).
# Dependency-free (stdlib ``csv``). Rows become one GFM Markdown table.
"""CSV -> GFM Markdown table parser."""

from __future__ import annotations

import csv
import io
import logging

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)


class CsvParser(BaseParser):
    """Parse CSV bytes into a GFM Markdown table."""

    def parse_into_text(self, content: bytes) -> Document:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")  # lenient fallback
        rows = list(csv.reader(io.StringIO(text)))
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            return Document(content="", metadata={"format": "csv", "parser": "builtin"})
        return Document(
            content=_csv_markdown(rows),
            metadata={"format": "csv", "parser": "builtin"},
        )


def _csv_markdown(rows: list[list[str]]) -> str:
    cols = max(len(r) for r in rows)
    lines = ["| " + " | ".join(_esc(c) for c in _pad(rows[0], cols)) + " |"]
    lines.append("| " + " | ".join(["---"] * cols) + " |")
    for r in rows[1:]:
        lines.append("| " + " | ".join(_esc(c) for c in _pad(r, cols)) + " |")
    return "\n".join(lines)


def _pad(row: list[str], cols: int) -> list[str]:
    return (row + [""] * cols)[:cols]


def _esc(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\n", " ") if cell else ""