# SPDX-License-Identifier: MIT
# Phase 6 (TXT/HTML/MHTML #5): tests. TXT is already the plain-text engine;
# HTML/MHTML samples are built in-memory with the stdlib.
"""Tests for the HTML and MHTML parsers."""

from __future__ import annotations

import pytest

from docreader.parser.html_parser import HtmlParser
from docreader.parser.mhtml_parser import MhtmlParser
from docreader.parser.registry import list_engines, parse_file


def test_html_strips_tags_keeps_heading_and_table() -> None:
    html = "<html><body><h1>Title</h1><p>Hello <b>world</b></p>" \
           "<table><tr><td>A</td><td>B</td></tr></table></body></html>"
    doc = HtmlParser().parse(html.encode())
    assert "# Title" in doc.content
    assert "Hello world" in doc.content
    assert "A | B" in doc.content


def test_html_skips_script_style() -> None:
    html = "<html><script>var x=1;</script><style>p{}</style><p>ok</p></html>"
    doc = HtmlParser().parse(html.encode())
    assert "ok" in doc.content
    assert "var x" not in doc.content


def test_html_registered_with_htm_alias() -> None:
    builtin = next(e for e in list_engines() if e["name"] == "builtin")
    assert {"html", "htm"}.issubset(set(builtin["file_types"]))
    doc = parse_file("a.htm", "htm", b"<p>hi</p>")
    assert "hi" in doc.content


def _mhtml_bytes() -> bytes:
    # a minimal MHTML archive: main text/html part carrying the quoted-printable
    # HTML body (a known char encoded later), plus a header.
    payload = b"<html><body><p>Archived page</p></body></html>"
    return (
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/related; boundary="B"\r\n\r\n'
        b"--B\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n\r\n" + payload + b"\r\n--B--\r\n"
    )


def test_mhtml_extracts_html_part() -> None:
    doc = MhtmlParser().parse(_mhtml_bytes())
    assert "Archived page" in doc.content


def test_mhtml_registered() -> None:
    builtin = next(e for e in list_engines() if e["name"] == "builtin")
    assert {"mhtml", "mht"}.issubset(set(builtin["file_types"]))
    doc = parse_file("a.mhtml", "mhtml", _mhtml_bytes())
    assert "Archived page" in doc.content