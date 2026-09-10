# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #1 DOCX).
# Dependency-free: DOCX is a ZIP of OOXML; we read word/document.xml via the
# stdlib (zipfile + xml.etree). Extracts paragraphs (<w:p>) and tables (<w:tbl>)
# into Markdown, keeping reading order, honoring heading styles, run emphasis,
# hyperlinks and merged table cells (gridSpan / vMerge). No python-docx /
# external dependency. Embedded binary/images are left to the vision pipeline.
"""DOCX (OOXML) -> Markdown parser."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.zip_safe import open_safe_zip, read_member

logger = logging.getLogger(__name__)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_HEADING_LEVELS: dict[str, int] = {
    "Title": 1,
    **{f"Heading{n}": n for n in range(1, 10)},
}


class DocxParser(BaseParser):
    """Parse a .docx archive into Markdown text (paragraphs + tables)."""

    def parse_into_text(self, content: bytes) -> Document:
        with open_safe_zip(content) as zf:
            names = zf.namelist()
            if "word/document.xml" not in names:
                raise ValueError("docx has no word/document.xml")
            xml_bytes = read_member(zf, "word/document.xml")
            rels = _read_document_rels(zf, names)

        root = ET.fromstring(xml_bytes)
        body = root.find(f"{_W}body")
        if body is None:
            return Document(content="", metadata={"format": "docx", "parser": "builtin"})

        blocks = [x for x in _walk_body(body, rels) if x]
        return Document(
            content="\n\n".join(blocks).strip(),
            metadata={"format": "docx", "parser": "builtin"},
        )


def _read_document_rels(zf, names: list[str]) -> dict[str, str]:
    """Read word/_rels/document.xml.rels into {relation-id: target}."""
    if "word/_rels/document.xml.rels" not in names:
        return {}
    try:
        root = ET.fromstring(read_member(zf, "word/_rels/document.xml.rels"))
    except ET.ParseError:
        return {}
    out = {}
    for el in root.iter():
        if _local(el.tag) == "Relationship":
            rid = el.get("Id")
            target = el.get("Target")
            if rid and target:
                out[rid] = target
    return out


def _walk_body(body: ET.Element, rels: dict[str, str]):
    for child in body:
        tag = _local(child.tag)
        if tag == "p":
            text, level = _paragraph_text(child, rels)
            if text:
                if level:
                    yield f"{'#' * level} {text.strip()}"
                else:
                    yield text
        elif tag == "tbl":
            text = _table_markdown(child, rels)
            if text:
                yield text
        elif tag == "sdt":
            sub = child.find(f"{_W}sdtContent")
            if sub is not None:
                yield from _walk_body(sub, rels)


def _paragraph_text(p: ET.Element, rels: dict[str, str]) -> tuple[str, int | None]:
    """Return (markdown_text, heading_level_or_None)."""
    level = _heading_level(p)
    plain: list[str] = []
    md: list[str] = []
    for child in p:
        tag = _local(child.tag)
        if tag == "pPr":
            continue
        if tag == "r":
            text, b, i = _run_segments(child)
            plain.append(text)
            md.append(_wrap_run(text, b, i))
        elif tag == "hyperlink":
            target = None
            rid = child.get(f"{_R}id")
            if rid:
                target = rels.get(rid)
            text, plain_part = _hyperlink_text(child)
            plain.append(text)
            if target:
                md.append(f"[{_show(text)}]({target})")
            else:
                md.append(text)
        elif tag in ("bookmarkStart", "bookmarkEnd", "commentRangeStart",
                     "commentRangeEnd", "proofErr", "lastRenderedPageBreak", "sectPr"):
            continue
    return ("".join(md), level) if not level else ("".join(plain), level)


def _heading_level(p: ET.Element) -> int | None:
    pPr = p.find(f"{_W}pPr")
    if pPr is None:
        return None
    style = pPr.find(f"{_W}pStyle")
    if style is None:
        return None
    val = style.get(_W + "val") or ""
    return _HEADING_LEVELS.get(val.strip())


def _run_segments(r: ET.Element) -> tuple[str, bool, bool]:
    rPr = r.find(f"{_W}rPr")
    bold = rPr is not None and rPr.find(f"{_W}b") is not None
    italic = rPr is not None and rPr.find(f"{_W}i") is not None
    parts: list[str] = []
    for el in r.iter():
        tag = _local(el.tag)
        if tag == "t":
            if el.text:
                parts.append(el.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag == "br":
            parts.append("\n")
    return "".join(parts), bold, italic


def _wrap_run(text: str, bold: bool, italic: bool) -> str:
    if not text:
        return text
    if bold and italic:
        return f"***{text}***"
    if bold:
        return f"**{text}**"
    if italic:
        return f"*{text}*"
    return text


def _hyperlink_text(hl: ET.Element) -> tuple[str, str]:
    """Return (plain_text, markdown_text) from a hyperlink's runs."""
    plain: list[str] = []
    md: list[str] = []
    for r in hl.findall(f"{_W}r"):
        text, b, i = _run_segments(r)
        plain.append(text)
        md.append(_wrap_run(text, b, i))
    return "".join(plain), "".join(md)


def _show(text: str) -> str:
    """Hyperlink display text: strip emphasis markers, keep content."""
    return text.strip()


def _table_markdown(tbl: ET.Element, rels: dict[str, str]) -> str:
    rows: list[list[str]] = []
    carry: dict[int, str] = {}
    for tr in tbl.iter(f"{_W}tr"):
        row_cells: list[str] = []
        col = 0
        for tc in tr.findall(f"{_W}tc"):
            span = _grid_span(tc)
            text = _tc_text(tc, rels)
            vmode = _vmerge_mode(tc)
            for _ in range(span):
                cur = col
                col += 1
                value = text
                if vmode == "restart":
                    carry[cur] = text
                elif vmode == "continue":
                    value = carry.get(cur, "")
                row_cells.append(value)
        if row_cells:
            rows.append(row_cells)
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


def _tc_text(tc: ET.Element, rels: dict[str, str]) -> str:
    paras = []
    for p in tc.findall(f"{_W}p"):
        text, _level = _paragraph_text(p, rels)
        # table cells are rendered as plain text (no emphasis markers)
        paras.append(_strip_emphasis(text))
    return " ".join(x for x in paras if x)


def _strip_emphasis(text: str) -> str:
    for marker in ("***", "**", "*"):
        text = text.replace(marker, "")
    return text.strip()


def _grid_span(tc: ET.Element) -> int:
    tcPr = tc.find(f"{_W}tcPr")
    if tcPr is None:
        return 1
    gs = tcPr.find(f"{_W}gridSpan")
    if gs is None:
        return 1
    try:
        return max(1, int(gs.get(_W + "val") or 1))
    except ValueError:
        return 1


def _vmerge_mode(tc: ET.Element) -> str | None:
    tcPr = tc.find(f"{_W}tcPr")
    if tcPr is None:
        return None
    vm = tcPr.find(f"{_W}vMerge")
    if vm is None:
        return None
    val = vm.get(_W + "val")
    if not val or val.lower() in ("continue", "cont", ""):
        return "continue"
    return "restart"


def _esc(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\n", " ") if cell else cell


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]