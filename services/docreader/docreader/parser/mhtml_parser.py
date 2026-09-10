# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #5: MHTML/MHT).
# Dependency-free (stdlib ``email``). MHTML is a MIME multipart archive saved by
# browsers; we pick the first ``text/html`` (or ``text/plain``) part and turn it
# into Markdown-ish text via the HTML parser.
"""MHTML (MIME HTML archive) -> text parser (stdlib email)."""

from __future__ import annotations

import logging
from email import policy
from email.parser import BytesParser

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.html_parser import _html_to_text

logger = logging.getLogger(__name__)


class MhtmlParser(BaseParser):
    """Parse MHTML/MHT bytes: pick the main HTML/text part and strip tags."""

    def parse_into_text(self, content: bytes) -> Document:
        msg = BytesParser(policy=policy.default).parsebytes(content)
        payload = _first_text_payload(msg)
        if payload is None:
            return Document(content="", metadata={"format": "mhtml", "parser": "builtin"})
        text, is_html = payload
        return Document(
            content=_html_to_text(text) if is_html else text,
            metadata={"format": "mhtml", "parser": "builtin"},
        )


def _first_text_payload(msg) -> tuple[str, bool] | None:
    if msg.is_multipart():
        for part in msg.iter_parts():
            got = _first_text_payload(part)
            if got:
                return got
        return None
    ctype = (msg.get_content_type() or "").lower()
    if ctype == "text/html":
        return _decode(msg), True
    if ctype == "text/plain":
        return _decode(msg), False
    return None


def _decode(msg) -> str:
    try:
        return msg.get_content()
    except Exception:  # noqa: BLE001  charset quirks
        raw = msg.get_payload(decode=True)
        return raw.decode("utf-8", errors="replace") if raw else ""