# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #3: PPTX).
# Dependency-free: PPTX is a ZIP of OOXML; we read each slide via stdlib
# (zipfile + xml.etree) and extract text paragraphs from drawing shapes.
"""PPTX (OOXML Presentation) -> Markdown text (text dealt out per slide)."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.zip_safe import read_member, open_safe_zip

logger = logging.getLogger(__name__)

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


class PptxParser(BaseParser):
    """Parse .pptx bytes into Markdown: slide headings + paragraph text."""

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

        return Document(
            content="\n\n".join(blocks).strip(),
            metadata={"format": "pptx", "parser": "builtin"},
        )


def _slide_block(name: str, text: str) -> str:
    num = int(re.search(r"slide(\d+)\.xml", name).group(1))
    return f"<!-- slide {num} -->\n{text.strip()}"


def _slide_text(xml_bytes: bytes) -> str:
    root = ET.fromstring(xml_bytes)
    paras: list[str] = []
    for p in root.iter(f"{_A}p"):
        t = "".join((el.text or "") for el in p.iter(f"{_A}t"))
        if t.strip():
            paras.append(t)
    return "\n".join(paras)