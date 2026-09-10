# SPDX-License-Identifier: MIT
# Phase 6 (EPUB/XMind #6): tests. Samples built in-memory with the stdlib.
"""Tests for the EPUB and XMind parsers."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from docreader.parser.epub_parser import EpubParser
from docreader.parser.registry import list_engines, parse_file
from docreader.parser.xmind_parser import XmindParser


def _epub_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", b"<x/>")
        zf.writestr("OEBPS/chapter1.xhtml", b"<html><body><h1>Chapter One</h1><p>some body text</p></body></html>")
        zf.writestr("OEBPS/chapter2.xhtml", b"<html><body><p>second chapter</p></body></html>")
    return buf.getvalue()


def test_epub_extracts_xhtml_parts() -> None:
    doc = EpubParser().parse(_epub_bytes())
    assert "Chapter One" in doc.content
    assert "some body text" in doc.content
    assert "second chapter" in doc.content


def test_epub_invalid_rejected() -> None:
    with pytest.raises(ValueError):
        EpubParser().parse(b"not a zip")


def test_epub_registered_and_routed() -> None:
    builtin = next(e for e in list_engines() if e["name"] == "builtin")
    assert "epub" in builtin["file_types"]
    doc = parse_file("b.epub", "epub", _epub_bytes())
    assert "Chapter One" in doc.content


def _xmind_json_bytes() -> bytes:
    data = [{"rootTopic": {"title": "Root", "children": {
        "attached": [{"title": "Child A", "children": {"attached": [{"title": "Grandchild"}]}},
                     {"title": "Child B"}]}}}]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("content.json", json.dumps(data).encode())
    return buf.getvalue()


def test_xmind_extracts_topic_tree() -> None:
    doc = XmindParser().parse(_xmind_json_bytes())
    assert "Root" in doc.content
    assert "Child A" in doc.content
    assert "Child B" in doc.content
    assert "Grandchild" in doc.content


def test_xmind_registered_and_routed() -> None:
    builtin = next(e for e in list_engines() if e["name"] == "builtin")
    assert "xmind" in builtin["file_types"]
    doc = parse_file("m.xmind", "xmind", _xmind_json_bytes())
    assert "Child A" in doc.content