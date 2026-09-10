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


def test_docx_registered_and_routed() -> None:
    engines = list_engines()
    docx = next(e for e in engines if e["name"] == "builtin")
    assert "docx" in docx["file_types"]
    doc = parse_file("sample.docx", "docx", _build_docx())
    assert "Hello DOCX" in doc.content