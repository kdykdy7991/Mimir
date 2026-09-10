# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #6: EPUB).
# Dependency-free: EPUB is a ZIP of (X)HTML documents; we strip each to text.
# Reading order follows the OPF package's <spine> (META-INF/container.xml ->
# package rootfile -> manifest+spine) and falls back to filename order when the
# package metadata is absent. No third-party epub reader.
"""EPUB -> text parser (stdlib zipfile + xml.etree + html stripping)."""

from __future__ import annotations

import logging
import posixpath
import xml.etree.ElementTree as ET

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.html_parser import _html_to_text
from docreader.parser.zip_safe import open_safe_zip, read_member

logger = logging.getLogger(__name__)

_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_OPF_NS = "{http://www.idpf.org/2007/opf}"
_XHTML_EXTS = (".xhtml", ".html", ".htm")


class EpubParser(BaseParser):
    """Parse .epub bytes: concatenate (X)HTML parts in spine reading order."""

    def parse_into_text(self, content: bytes) -> Document:
        with open_safe_zip(content) as zf:
            names = zf.namelist()
            blocks = self._spine_blocks(zf, names)
            if not blocks:
                fallback = self._filename_sorted_blocks(zf, names)
                if not fallback:
                    raise ValueError("epub has no content parts")
                blocks = fallback
        return Document(
            content="\n\n".join(blocks).strip(),
            metadata={"format": "epub", "parser": "builtin"},
        )

    @staticmethod
    def _content_part(zf, name: str) -> str | None:
        """Strip one (X)HTML part to text, or None if it is not a content doc."""
        if not name.lower().endswith(_XHTML_EXTS):
            return None
        raw = read_member(zf, name).decode("utf-8", errors="replace")
        text = _html_to_text(raw)
        return text.strip() or None

    @classmethod
    def _spine_blocks(cls, zf, names: list[str]):
        """Follow spine reading order; return [] when package metadata is absent."""
        opf_path = cls._opf_path(zf, names)
        if opf_path is None:
            return []
        try:
            opf_root = ET.fromstring(read_member(zf, opf_path))
        except ET.ParseError:
            return []
        hrefs = {}
        for item in opf_root.iter(f"{_OPF_NS}item"):
            item_id = item.get("id")
            href = item.get("href")
            if item_id and href:
                hrefs[item_id] = href
        spine = opf_root.find(f"{_OPF_NS}spine")
        if spine is None:
            return []
        opf_dir = posixpath.dirname(opf_path)
        blocks = []
        for itemref in spine.iter(f"{_OPF_NS}itemref"):
            ref = itemref.get("idref")
            href = hrefs.get(ref) if ref else None
            if not href:
                continue
            full = _resolve(opf_dir, href)
            if full in names:
                text = cls._content_part(zf, full)
                if text:
                    blocks.append(text)
        return blocks

    @staticmethod
    def _opf_path(zf, names: list[str]) -> str | None:
        if "META-INF/container.xml" not in names:
            return None
        try:
            root = ET.fromstring(read_member(zf, "META-INF/container.xml"))
        except ET.ParseError:
            return None
        for rootfile in root.iter(f"{_CONTAINER_NS}rootfile"):
            full_path = rootfile.get("full-path")
            if full_path and full_path in names and full_path.endswith(".opf"):
                return full_path
        # tolerate OPF paths that were rewritten without the extension
        for rootfile in root.iter(f"{_CONTAINER_NS}rootfile"):
            full_path = rootfile.get("full-path")
            if full_path:
                for candidate in (full_path, full_path + ".opf"):
                    if candidate in names and candidate.endswith(".opf"):
                        return candidate
        return None

    @staticmethod
    def _filename_sorted_blocks(zf, names: list[str]):
        parts = sorted(n for n in names if n.lower().endswith(_XHTML_EXTS))
        blocks = []
        for n in parts:
            text = _html_to_text(read_member(zf, n).decode("utf-8", errors="replace"))
            if text.strip():
                blocks.append(text)
        return blocks


def _resolve(opf_dir: str, href: str) -> str:
    """Resolve a manifest href relative to the OPF package directory."""
    base = opf_dir or ""
    if base and not base.endswith("/"):
        base += "/"
    return posixpath.normpath(base + href)