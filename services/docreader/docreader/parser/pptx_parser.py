# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #3: PPTX).
# Dependency-free: PPTX is a ZIP of OOXML; we read each slide via stdlib
# (zipfile + xml.etree), extract paragraph text and GFM tables from drawing
# shapes, then append speaker notes. No third-party package.
"""PPTX (OOXML Presentation) -> Markdown text (per-slide blocks + notes)."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.zip_safe import open_safe_zip, read_member

logger = logging.getLogger(__name__)

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


class PptxParser(BaseParser):
    """Parse .pptx bytes into Markdown: slide blocks + tables + notes."""

    def parse_into_text(self, content: bytes) -> Document:
        with open_safe_zip(content) as zf:
            names = zf.namelist()
            slides = sorted(
                (n for n in names if re.match(r"^ppt/slides/slide\d+\.xml$", n)),
                key=lambda n: int(re.search(r"slide(\d+)\.xml", n).group(1)),
            )
            if not slides:
                raise ValueError("pptx has no ppt/slides")
            blocks = []
            for n in slides:
                text = _slide_text(read_member(zf, n))
                if text.strip():
                    blocks.append(_slide_block(n, text))
            for n in sorted(
                (m for m in names if re.match(r"^ppt/notesSlides/notesSlide\d+\.xml$", m)),
                key=lambda m: int(re.search(r"notesSlide(\d+)\.xml", m).group(1)),
            ):
                notes = _slide_text(read_member(zf, n))
                if notes.strip():
                    num = int(re.search(r"notesSlide(\d+)\.xml", n).group(1))
                    blocks.append(f"<!-- notes slide {num} -->\n{notes.strip()}")

        return Document(
            content="\n\n".join(blocks).strip(),
            metadata={"format": "pptx", "parser": "builtin"},
        )


def _slide_block(name: str, text: str) -> str:
    num = int(re.search(r"slide(\d+)\.xml", name).group(1))
    return f"<!-- slide {num} -->\n{text.strip()}"


def _slide_text(xml_bytes: bytes) -> str:
    """Extract paragraphs (excluding table cells) + GFM tables from a slide."""
    root = ET.fromstring(xml_bytes)
    tbls = list(root.iter(f"{_A}tbl"))
    table_paras = set()
    para_ids: set[int] = set()
    for tbl in tbls:
        for p in tbl.iter(f"{_A}p"):
            para_ids.add(id(p))
    paras: list[str] = []
    for el in root.iter(f"{_A}p"):
        if id(el) in para_ids:
            continue
        t = "".join((x.text or "") for x in el.iter(f"{_A}t"))
        if t.strip():
            paras.append(t)
    blocks: list[str] = []
    if paras:
        blocks.append("\n".join(paras))
    for tbl in tbls:
        md = _table_markdown(tbl)
        if md:
            blocks.append(md)
    return "\n\n".join(blocks)


def _table_markdown(tbl: ET.Element) -> str:
    rows: list[list[str]] = []
    for tr in tbl.iter(f"{_A}tr"):
        cells: list[str] = []
        for tc in tr.iter(f"{_A}tc"):
            t = "".join((x.text or "") for x in tc.iter(f"{_A}t"))
            cells.append(t)
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < cols:
            r.append("")
    lines = ["| " + " | ".join(_esc(c) for c in rows[0]) + " |",
             "| " + " | ".join(["---"] * cols) + " |"]
    lines += ["| " + " | ".join(_esc(c) for c in r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _esc(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\n", " ") if cell else ""