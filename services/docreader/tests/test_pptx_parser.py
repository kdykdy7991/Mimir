# SPDX-License-Identifier: MIT
# Phase 6 (PPTX #3): tests. Sample built in-memory with stdlib (zipfile+xml).
"""Tests for the PPTX -> Markdown parser."""

from __future__ import annotations

import io
import zipfile

import pytest

from docreader.parser.pptx_parser import PptxParser
from docreader.parser.registry import list_engines, parse_file

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


def _slide(title: str, *bullets: str) -> str:
    runs = "".join(f"<a:p><a:r><a:t xml:space=\"preserve\">{t}</a:t></a:r></a:p>" for t in (title, *bullets))
    return (
        f'<?xml version="1.0"?>'
        f'<p:sld xmlns:p="{_P[1:-1]}" xmlns:a="{_A[1:-1]}">'
        f'<p:cSld><p:spTree>{runs}</p:spTree></p:cSld></p:sld>'
    )


def _pptx_bytes(n_slides=2) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<x/>")
        zf.writestr("ppt/presentation.xml", b"<x/>")
        zf.writestr("ppt/slides/slide1.xml", _slide("Title One", "bullet a").encode())
        zf.writestr("ppt/slides/slide2.xml", _slide("Title Two", "bullet b", "bullet c").encode())
    return buf.getvalue()


def test_pptx_extracts_slides_in_order() -> None:
    doc = PptxParser().parse(_pptx_bytes())
    assert "Title One" in doc.content
    assert "Title Two" in doc.content
    assert "bullet c" in doc.content
    # slide order preserved
    assert doc.content.index("Title One") < doc.content.index("Title Two")
    assert "<!-- slide 1 -->" in doc.content and "<!-- slide 2 -->" in doc.content


def test_pptx_invalid_rejected() -> None:
    with pytest.raises(ValueError):
        PptxParser().parse(b"not a zip")


def test_pptx_registered_and_routed() -> None:
    builtin = next(e for e in list_engines() if e["name"] == "builtin")
    assert "pptx" in builtin["file_types"]
    doc = parse_file("p.pptx", "pptx", _pptx_bytes())
    assert "Title One" in doc.content