# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #6: XMind).
# Dependency-free: .xmind is a ZIP; older versions carry content.xml, newer
# (XMind ZEN) carry content.json. We read whichever exists and pull out topic
# titles recursively.
"""XMind mind-map -> text parser (stdlib zipfile + json/xml)."""

from __future__ import annotations

import io
import json
import logging
import zipfile
import xml.etree.ElementTree as ET

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)


class XmindParser(BaseParser):
    """Parse .xmind bytes: extract topic titles/labels from content.json or content.xml."""

    def parse_into_text(self, content: bytes) -> Document:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = zf.namelist()
                text = ""
                if "content.json" in names:
                    text = _json_topics(json.loads(zf.read("content.json").decode("utf-8", errors="replace")))
                elif "content.xml" in names:
                    root = ET.fromstring(zf.read("content.xml"))
                    text = _xml_topics(root)
                else:
                    raise ValueError("xmind has neither content.json nor content.xml")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"invalid xmind archive: {exc}") from exc
        return Document(content=text, metadata={"format": "xmind", "parser": "builtin"})


def _json_topics(obj) -> str:
    out: list[str] = []

    def walk(node, depth):
        if not isinstance(node, dict):
            return
        title = node.get("title")
        if title:
            out.append(("  " * depth) + str(title))
        for child in node.get("children", {}).values():
            for c in (child if isinstance(child, list) else [child]) or []:
                walk(c, depth + 1)

    if isinstance(obj, list):
        for sheet in obj:
            root = sheet.get("rootTopic")
            if root:
                walk(root, 0)
    elif isinstance(obj, dict):
        root = obj.get("rootTopic")
        if root:
            walk(root, 0)
    return "\n".join(out)


def _xml_topics(root: ET.Element) -> str:
    out: list[str] = []

    def walk(el, depth):
        title = el.find("{http://www.xmind.net/xmind/xpict}title")
        t = title.text if title is not None and title.text else "(untitled)"
        out.append(("  " * depth) + t)
        for c in el.findall("{http://www.xmind.net/xmind/xpict}topics") or []:
            for tc in c:
                walk(tc, depth + 1)

    for topic in root.iter("{http://www.xmind.net/xmind/xpict}topic"):
        walk(topic, 0)
        break
    return "\n".join(out)