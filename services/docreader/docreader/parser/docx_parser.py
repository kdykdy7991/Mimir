# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #1 DOCX).
# Dependency-free: DOCX is a ZIP of OOXML; we read word/document.xml via the
# stdlib (zipfile + xml.etree). Extracts paragraphs (<w:p>) and tables (<w:tbl>)
# into Markdown, keeping reading order. No python-docx / external dependency.
"""DOCX (OOXML) -> Markdown parser."""

from __future__ import annotations

import io
import logging
import zipfile
import xml.etree.ElementTree as ET

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocxParser(BaseParser):
    """Parse a .docx archive into Markdown text (paragraphs + tables)."""

    def parse_into_text(self, content: bytes) -> Document:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = zf.namelist()
                if "word/document.xml" not in names:
                    raise ValueError("docx has no word/document.xml")
                xml_bytes = zf.read("word/document.xml")
        except zipfile.BadZipFile as exc:  # not a docx (or not a zip)
            raise ValueError(f"invalid docx archive: {exc}") from exc

        root = ET.fromstring(xml_bytes)
        body = root.find(f"{_W}body")
        if body is None:
            return Document(content="", metadata={"format": "docx", "parser": "builtin"})

        blocks = [x for x in _walk_body(body) if x]
        return Document(
            content="\n\n".join(blocks).strip(),
            metadata={"format": "docx", "parser": "builtin"},
        )


def _walk_body(body: ET.Element):
    for child in body:
        tag = _local(child.tag)
        if tag == "p":
            text = _paragraph_text(child)
            if text:
                yield text
        elif tag == "tbl":
            text = _table_markdown(child)
            if text:
                yield text
        elif tag == "sdt":
            # structured document tags may wrap normal blocks
            sub = child.find(f"{_W}sdtContent")
            if sub is not None:
                yield from _walk_body(sub)


def _paragraph_text(p: ET.Element) -> str:
    parts: list[str] = []
    for child in p.iter():
        tag = _local(child.tag)
        if tag == "t":
            if child.text:
                parts.append(child.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag == "br":
            parts.append("\n")
    return "".join(parts).strip()


def _table_markdown(tbl: ET.Element) -> str:
    cols = 0
    rows: list[list[str]] = []
    for tr in tbl.iter(f"{_W}tr"):
        cells: list[str] = []
        for tc in tr.findall(f"{_W}tc"):
            paras = [_paragraph_text(p) for p in tc.findall(f"{_W}p")]
            cells.append(" ".join(x for x in paras if x))
        if cells:
            cols = max(cols, len(cells))
            rows.append(cells)
    if not rows:
        return ""
    for r in rows:
        while len(r) < cols:
            r.append("")
    lines = ["| " + " | ".join(rows[0]) + " |",
             "| " + " | ".join(["---"] * cols) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]