# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #2: XLSX).
# Dependency-free: XLSX is a ZIP of OOXML; we read shared strings and each
# worksheet via stdlib (zipfile + xml.etree). Cells (shared / inline-string /
# numeric) are laid out row-by-row and emitted as GFM Markdown tables.
"""XLSX (OOXML Spreadsheet) -> GFM Markdown tables."""

from __future__ import annotations

import io
import logging
import zipfile
import xml.etree.ElementTree as ET

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)

_W = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class XlsxParser(BaseParser):
    """Parse .xlsx bytes into GFM Markdown tables (one block per sheet)."""

    def parse_into_text(self, content: bytes) -> Document:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = zf.namelist()
                shared = self._read_shared_strings(zf, names)
                sheet_files = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
                if not sheet_files and "xl/workbook.xml" not in names:
                    raise ValueError("xlsx has no worksheets")
                blocks = []
                for sf in sheet_files:
                    block = self._sheet_markdown(zf.read(sf), shared)
                    if block:
                        blocks.append(block)
        except zipfile.BadZipFile as exc:
            raise ValueError(f"invalid xlsx archive: {exc}") from exc

        return Document(
            content="\n\n".join(blocks).strip(),
            metadata={"format": "xlsx", "parser": "builtin"},
        )

    @staticmethod
    def _read_shared_strings(zf, names) -> list[str]:
        if "xl/sharedStrings.xml" not in names:
            return []
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        out = []
        for si in root.iter(f"{_W}si"):
            text = "".join((t.text or "") for t in si.iter(f"{_W}t"))
            out.append(text)
        return out

    @staticmethod
    def _sheet_markdown(xml_bytes: bytes, shared: list[str]) -> str:
        root = ET.fromstring(xml_bytes)
        table: list[tuple[int, list[str]]] = []
        for row_el in root.iter(f"{_W}row"):
            row_num = int(row_el.get("r") or 0)
            cells: list[tuple[int, str]] = []
            for c in row_el.findall(f"{_W}c"):
                col = _col_index(c.get("r") or "")
                val = XlsxParser._cell_value(c, shared)
                cells.append((col, val))
            if not cells:
                continue
            cells.sort()
            width = cells[-1][0] + 1
            arr = [""] * width
            for idx, val in cells:
                if 0 <= idx < width:
                    arr[idx] = val
            table.append((row_num, arr))
        # split on fully-empty rows -> separate markdown tables
        all_rows = [r for _, r in table]
        blocks = []
        current = []
        for r in all_rows:
            if any(cell.strip() for cell in r):
                current.append(r)
            elif current:
                blocks.append(_grid_markdown(current))
                current = []
        if current:
            blocks.append(_grid_markdown(current))
        return "\n\n".join(blocks)

    @staticmethod
    def _cell_value(c: ET.Element, shared: list[str]) -> str:
        typ = c.get("t")
        v = c.find(f"{_W}v")
        if typ == "s" and v is not None and v.text is not None:
            idx = int(v.text)
            return shared[idx] if 0 <= idx < len(shared) else ""
        if typ == "inlineStr":
            is_ = c.find(f"{_W}is")
            if is_ is not None:
                return "".join((t.text or "") for t in is_.iter(f"{_W}t"))
        if v is not None and v.text is not None:
            return v.text
        return ""


def _col_index(ref: str) -> int:
    letters = ""
    for ch in ref:
        if ch.isalpha():
            letters += ch
        else:
            break
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1


def _grid_markdown(rows: list[list[str]]) -> str:
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