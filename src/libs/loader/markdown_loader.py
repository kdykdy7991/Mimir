"""
Markdown loader implementation.

Loads ``.md`` / ``.markdown`` files into a ``core.types.Document``
that satisfies the C3 contract:

- ``metadata.source_path`` is always set.
- ``metadata.doc_type`` is ``"markdown"``.
- ``metadata.title`` is the first level-1 heading, falling back to the
  file name stem when the document has no ``#`` heading.
- ``metadata.heading_outline`` is a list of ``{"level": n, "text": ...}``
  entries for every ATX heading, in document order. Fenced code blocks
  are skipped so ``# comment`` inside a code block is not treated as a
  heading.

Structure preservation
----------------------
The document's text is the Markdown itself: headings, lists, tables,
blockquotes and fenced code blocks are kept verbatim. We never
parse-and-re-render Markdown, so no structural fidelity is lost.
Normalisation is limited to **line endings only** — Markdown正文中的
空格与空行原样保留,因为:

- 行尾两个空格是 Markdown 硬换行(hard line break),`rstrip()` 会删除它;
- fenced code block 内的行尾空格与连续空行属于代码内容,不应被修改。

具体只做:

- decode as UTF-8, tolerating a leading BOM (``utf-8-sig``);
- normalise ``\\r\\n`` / ``\\r`` to ``\\n``.

(不再做行尾 ``rstrip()`` / 空行折叠 / 首尾裁剪。)

Encoding / binary safety
------------------------
- A file that cannot be decoded as UTF-8 raises ``LoaderError`` with a
  locatable message — nothing is written to any index.
- A file containing NUL bytes (a binary file disguised as Markdown)
  raises ``LoaderError``.
- An empty / whitespace-only file raises ``LoaderError``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from src.core.types import Document
from src.libs.loader.base_loader import BaseLoader, LoaderError

# Truncation length for the per-doc hash used as ``Document.id`` — matches
# PdfLoader so both loaders emit ids of the same shape/width.
DOC_HASH_LEN = 16

# Default encoding: ``utf-8-sig`` decodes a leading UTF-8 BOM transparently.
DEFAULT_ENCODING = "utf-8-sig"

# Fence markers that open/close a fenced code block (CommonMark).
_FENCE_MARKERS = ("```", "~~~")

# ATX heading: up to three leading spaces (so indented code ``    # x`` is
# excluded), 1-6 ``#``, then mandatory whitespace (so ``#foo`` is not a
# heading). ``re`` matches on the *raw* line to preserve the leading-space
# count that ``line.strip()`` would erase.
_ATX_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+)$")


class MarkdownLoader(BaseLoader):
    """
    Load Markdown files into ``Document`` objects.

    Args:
        encoding: Codec used to decode the file. Defaults to
            ``utf-8-sig`` (UTF-8, tolerating a BOM).
    """

    def __init__(self, encoding: str = DEFAULT_ENCODING) -> None:
        self.encoding = encoding

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load(self, path: str) -> Document:
        file_path = self._require_file(path)
        raw = file_path.read_bytes()

        # Binary safety: NUL bytes almost never appear in real UTF-8
        # text — catching them here rejects PDFs / executables disguised
        # as Markdown before any decode work.
        if b"\x00" in raw:
            raise LoaderError(
                f"Refusing to load binary file as Markdown: {file_path}",
            )

        try:
            text = raw.decode(self.encoding)
        except UnicodeDecodeError as exc:
            raise LoaderError(
                f"Failed to decode Markdown file {file_path} as "
                f"{self.encoding}: {exc}",
            ) from exc

        normalized = self._normalize(text)
        if not normalized.strip():
            raise LoaderError(
                f"Refusing to load empty Markdown document: {file_path}",
            )

        outline = _heading_outline(normalized)
        title = _first_h1(outline) or file_path.stem

        doc_hash = hashlib.sha256(raw).hexdigest()
        metadata: dict[str, Any] = {
            "source_path": str(file_path),
            "doc_type": "markdown",
            "doc_hash": doc_hash,
            "title": title,
            "heading_outline": outline,
            # No ``page`` / ``images`` keys: Markdown has no pagination and
            # image references are URLs the loader does not download.
        }
        return Document(
            id=doc_hash[:DOC_HASH_LEN],
            text=normalized,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(text: str) -> str:
        """Normalise line endings only; leave all other bytes untouched.

        Markdown hard line breaks (two trailing spaces), blank-line runs
        and indentation inside fenced code blocks are **content** — they
        must survive verbatim, so no ``rstrip()`` / blank-collapse here.
        The UTF-8 BOM is already stripped by ``utf-8-sig`` decoding.
        """
        return text.replace("\r\n", "\n").replace("\r", "\n")


def _heading_outline(text: str) -> list[dict[str, Any]]:
    """Extract the ATX-heading outline, skipping fenced code blocks.

    A heading is ``^ {0,3}#{1,6} <text>`` — up to three leading spaces
    (so indented code ``    # x`` is excluded), ``#`` must be followed by
    whitespace (so ``#foo`` is excluded). Returns a list of
    ``{"level": n, "text": stripped_text}`` in document order.
    """
    outline: list[dict[str, Any]] = []
    in_fence: str | None = None
    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        # Fence state machine. Inside a fence, every line is literal —
        # headings there are not headings.
        if in_fence is None:
            for marker in _FENCE_MARKERS:
                if stripped.startswith(marker):
                    in_fence = marker
                    break
            if in_fence is not None:
                continue
        else:
            if stripped.startswith(in_fence):
                in_fence = None
            continue

        match = _ATX_HEADING_RE.match(raw_line)
        if match is not None:
            outline.append(
                {"level": len(match.group(1)), "text": match.group(2).strip()},
            )
    return outline


def _first_h1(outline: list[dict[str, Any]]) -> str | None:
    """Text of the first level-1 heading, or ``None`` if there is none."""
    for entry in outline:
        if entry["level"] == 1:
            return entry["text"]
    return None


__all__ = [
    "DEFAULT_ENCODING",
    "DOC_HASH_LEN",
    "MarkdownLoader",
]
