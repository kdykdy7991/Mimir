# SPDX-License-Identifier: MIT
# Phase 6 (DOCX #1): parser + its acceptance test. Sample docx is generated
# in-memory with the stdlib (zipfile + ElementTree) — no python-docx dependency.
"""Tests for the DOCX (OOXML) -> Markdown parser."""

from __future__ import annotations

import zlib
import zipfile

import pytest

from docreader.parser.docx_parser import DocxParser
from docreader.parser.registry import list_engines, parse_file

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _t(p: str) -> str:
    return f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>"


def _cell(text: str) -> str:
    return f"<w:tc>{_t(text)}</w:tc>"


def _build_docx() -> bytes:
    def row(cells) -> str:
        return "<w:tr>" + "".join(_cell(c) for c in cells) + "</w:tr>"
    table = "<w:tbl>" + row(["H1", "H2"]) + row(["A", "B"]) + "</w:tbl>"
    body = _t("Hello DOCX") + "<w:sectPr/>" + table
    xml = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W[1:-1]}"><w:body>{body}</w:body></w:document>'
    )
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", xml.encode())
    return buf.getvalue()


def test_docx_paragraph_and_table_to_markdown() -> None:
    doc = DocxParser().parse(_build_docx())
    assert "Hello DOCX" in doc.content
    assert "| H1 | H2 |" in doc.content
    assert "| A | B |" in doc.content
    assert "| --- |" in doc.content
    assert doc.metadata["format"] == "docx"


def test_docx_invalid_archive_rejected() -> None:
    with pytest.raises(ValueError):
        DocxParser().parse(b"not a zip")


def test_docx_missing_document_xml_rejected() -> None:
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/other.xml", b"<x/>")
    with pytest.raises(ValueError):
        DocxParser().parse(buf.getvalue())


_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _build_rich_docx() -> bytes:
    heading = (
        f'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
        f'<w:r><w:t>My Heading</w:t></w:r></w:p>'
    )
    bold = f'<w:p><w:r><w:rPr><w:b/><w:i/></w:rPr><w:t>bold italic</w:t></w:r></w:p>'
    link = (
        f'<w:p><w:hyperlink r:id="rId5"><w:r><w:t>click me</w:t></w:r></w:hyperlink></w:p>'
    )
    # table: row1 gridSpan=2, then a vMerge restart + continue across rows 2-3
    table = (
        '<w:tbl>'
        f'<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>{_t("span")}</w:tc></w:tr>'
        f'<w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr>{_t("merged col")}</w:tc>'
        f'<w:tc>{_t("B")}</w:tc></w:tr>'
        f'<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr>{_t("")}</w:tc>'
        f'<w:tc>{_t("C")}</w:tc></w:tr>'
        '</w:tbl>'
    )
    body = heading + bold + link + table + "<w:sectPr/>"
    xml = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W[1:-1]}" xmlns:r="{_R[1:-1]}">'
        f'<w:body>{body}</w:body></w:document>'
    )
    rels = (
        f'<?xml version="1.0"?><Relationships xmlns="{_PKG[1:-1]}">'
        '<Relationship Id="rId5" Type="x/hyperlink" '
        'Target="https://example.com/?a=1"/>'
        '</Relationships>'
    ).encode()
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", xml.encode())
        zf.writestr("word/_rels/document.xml.rels", rels)
    return buf.getvalue()


def test_docx_heading_bold_hyperlink_merged_table() -> None:
    doc = DocxParser().parse(_build_rich_docx())
    assert "## My Heading" in doc.content
    assert "***bold italic***" in doc.content
    assert "[click me](https://example.com/?a=1)" in doc.content
    # gridSpan repeated cell across two columns
    assert "| span | span |" in doc.content
    # vMerge continues the restart cell's value down the covered row
    assert "| merged col | B |" in doc.content
    assert "| merged col | C |" in doc.content


def test_docx_registered_and_routed() -> None:
    engines = list_engines()
    docx = next(e for e in engines if e["name"] == "builtin")
    assert "docx" in docx["file_types"]
    doc = parse_file("sample.docx", "docx", _build_docx())
    assert "Hello DOCX" in doc.content