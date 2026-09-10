# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #2: XLSX).
# Dependency-free: XLSX is a ZIP of OOXML; we read shared strings and each
# worksheet via stdlib (zipfile + xml.etree). Sheet order and names come from
# xl/workbook.xml (+ relationships), cells (shared / inline-string / numeric /
# boolean / formula cached result) are laid out row-by-row honoring merged cell
# ranges, and each sheet is emitted as GFM Markdown tables.
"""XLSX (OOXML Spreadsheet) -> GFM Markdown tables."""

from __future__ import annotations

import logging
import posixpath
import xml.etree.ElementTree as ET

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.zip_safe import open_safe_zip, read_member

logger = logging.getLogger(__name__)

_W = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class XlsxParser(BaseParser):
    """Parse .xlsx bytes into GFM Markdown tables (one block per sheet)."""

    def parse_into_text(self, content: bytes) -> Document:
        with open_safe_zip(content) as zf:
            names = zf.namelist()
            shared = self._read_shared_strings(zf, names)
            ordered = self._sheet_order(zf, names)
            if ordered:
                sheet_pairs = ordered
            else:
                sheet_files = sorted(
                    n for n in names
                    if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
                )
                if not sheet_files and "xl/workbook.xml" not in names:
                    raise ValueError("xlsx has no worksheets")
                sheet_pairs = [(None, sf) for sf in sheet_files]
            blocks = []
            for name, sf in sheet_pairs:
                if sf not in names:
                    continue
                block = self._sheet_markdown(read_member(zf, sf), shared, name)
                if block:
                    blocks.append(block)

        return Document(
            content="\n\n".join(blocks).strip(),
            metadata={"format": "xlsx", "parser": "builtin"},
        )

    @staticmethod
    def _read_shared_strings(zf, names) -> list[str]:
        if "xl/sharedStrings.xml" not in names:
            return []
        root = ET.fromstring(read_member(zf, "xl/sharedStrings.xml"))
        out = []
        for si in root.iter(f"{_W}si"):
            text = "".join((t.text or "") for t in si.iter(f"{_W}t"))
            out.append(text)
        return out

    @staticmethod
    def _read_workbook_rels(zf, names) -> dict[str, str]:
        """relationship id -> normalized xl/... target path."""
        if "xl/_rels/workbook.xml.rels" not in names:
            return {}
        try:
            root = ET.fromstring(read_member(zf, "xl/_rels/workbook.xml.rels"))
        except ET.ParseError:
            return {}
        out = {}
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "Relationship":
                rid = el.get("Id")
                target = el.get("Target")
                if rid and target:
                    if target.startswith("/"):
                        full = posixpath.normpath(target.lstrip("/"))
                    else:
                        full = posixpath.normpath("xl/" + target)
                    out[rid] = full
        return out

    @classmethod
    def _sheet_order(cls, zf, names) -> list[tuple[str | None, str]]:
        """Return (sheet-name, sheet-path) pairs in workbook order, or []."""
        if "xl/workbook.xml" not in names:
            return []
        try:
            root = ET.fromstring(read_member(zf, "xl/workbook.xml"))
        except ET.ParseError:
            return []
        sheets = root.find(f"{_W}sheets")
        if sheets is None:
            return []
        rels = cls._read_workbook_rels(zf, names)
        pairs = []
        for s in sheets.findall(f"{_W}sheet"):
            name = s.get("name")
            rid = s.get(f"{_R}id")
            target = rels.get(rid) if rid else None
            if target:
                pairs.append((name, target))
        return pairs

    @staticmethod
    def _sheet_markdown(xml_bytes: bytes, shared: list[str], name: str | None = None) -> str:
        root = ET.fromstring(xml_bytes)
        merges = XlsxParser._merged_cells(root)
        rows: dict[int, dict[int, str]] = {}
        for row_el in root.iter(f"{_W}row"):
            row_num = int(row_el.get("r") or 0)
            if row_num <= 0:
                continue
            cells: dict[int, str] = {}
            for c in row_el.findall(f"{_W}c"):
                col = _col_index(c.get("r") or "")
                cells[col] = XlsxParser._cell_value(c, shared)
            if cells:
                rows[row_num] = cells
        XlsxParser._expand_merges(rows, merges)
        if not rows:
            return ""
        max_col = max(c for cells in rows.values() for c in cells) if any(cells for cells in rows.values()) else -1
        if max_col < 0:
            return ""
        width = max_col + 1
        ordered = []
        for rn in sorted(rows):
            arr = [""] * width
            for col, val in rows[rn].items():
                arr[col] = val
            ordered.append(arr)
        # split on fully-empty rows -> separate markdown tables
        blocks = []
        current = []
        for r in ordered:
            if any(cell.strip() for cell in r):
                current.append(r)
            elif current:
                blocks.append(_grid_markdown(current))
                current = []
        if current:
            blocks.append(_grid_markdown(current))
        body = "\n\n".join(blocks)
        if name:
            return f"## {name}\n\n{body}"
        return body

    @staticmethod
    def _merged_cells(root: ET.Element) -> list[tuple[int, int, int, int]]:
        out = []
        for mc in root.iter(f"{_W}mergeCell"):
            ref = mc.get("ref")
            if ref:
                out.append(_parse_range(ref))
        return out

    @staticmethod
    def _expand_merges(
        rows: dict[int, dict[int, str]], merges: list[tuple[int, int, int, int]]
    ) -> None:
        for r1, c1, r2, c2 in merges:
            tl = (rows.get(r1) or {}).get(c1) or ""
            if not tl:
                continue
            for rr in range(r1, r2 + 1):
                cellrow = rows.setdefault(rr, {})
                for cc in range(c1, c2 + 1):
                    if cc not in cellrow:
                        cellrow[cc] = tl

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
        if typ == "b":
            if v is not None and v.text is not None:
                return "TRUE" if v.text.strip() not in ("", "0", "false", "FALSE") else "FALSE"
            return ""
        if v is not None and v.text is not None:
            # covers numeric, formula cached <v>, and t="str" formula results
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


def _split_ref(ref: str) -> tuple[int, int]:
    letters = ""
    row_s = ""
    for ch in ref:
        if ch.isalpha():
            letters += ch
        else:
            row_s += ch
    col = _col_index(letters)
    row = int(row_s) if row_s.strip().isdigit() else 0
    return col, row


def _parse_range(ref: str) -> tuple[int, int, int, int]:
    if ":" not in ref:
        col, row = _split_ref(ref)
        return row, col, row, col
    a, b = ref.split(":", 1)
    ca, ra = _split_ref(a)
    cb, rb = _split_ref(b)
    c1, c2 = sorted((ca, cb))
    r1, r2 = sorted((ra, rb))
    return r1, c1, r2, c2


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


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]