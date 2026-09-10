# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #5: HTML).
# Dependency-free (stdlib ``html.parser``). Strips tags to readable text:
# headings get ``#`` prefixes, block elements start new paragraphs, and table
# cells are kept inline with `` | `` separators.
"""HTML -> Markdown-ish text parser."""

from __future__ import annotations

import logging
from html.parser import HTMLParser

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)

_HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
_NEWLINE_ON_END = {"p", "div", "section", "header", "footer", "li", "tr", "pre",
                   "blockquote", "table", "br", "br/", "ul", "ol"} | set(_HEADING_TAGS)
_SKIP = {"script", "style", "head", "meta", "title", "noscript"}


class _Extract(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._buf = ""
        self._skip_depth = 0
        self._cell = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip_depth += 1
        if self._skip_depth:
            return
        if tag in ("td", "th"):
            self._cell = True
            self._buf += " | "
        elif tag in _HEADING_TAGS:
            self._buf += _HEADING_TAGS[tag] + " "
        elif tag == "br":
            self._flush()

    def handle_endtag(self, tag):
        if self._skip_depth:
            if tag in _SKIP:
                self._skip_depth -= 1
            return
        if tag in ("p", "div", "section", "header", "footer", "li", "tr",
                   "pre", "blockquote", "table", "ul", "ol") or tag in _HEADING_TAGS:
            self._flush()
        elif tag == "br":
            self._flush()

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._buf += data

    def _flush(self):
        text = self._buf.strip()
        self._buf = ""
        if text:
            self.out.append(text.replace("  ", " "))


def _html_to_text(raw: str) -> str:
    p = _Extract()
    p.feed(raw)
    p.close()
    p._flush()
    seen = []
    for line in p.out:
        if line and (not seen or seen[-1] != line):
            seen.append(line)
    return "\n\n".join(seen).strip()


class HtmlParser(BaseParser):
    """Parse HTML/HTM bytes into readable Markdown-ish text."""

    def parse_into_text(self, content: bytes) -> Document:
        text = None
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        return Document(content=_html_to_text(text or content.decode("latin-1")),
                        metadata={"format": "html", "parser": "builtin"})