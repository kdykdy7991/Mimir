"""
Shared chunk ordering + locator helpers (task book B1).

The task book requires that every chunk list — the MCP ``get_document_chunks``
tool and the new Web ``GET /documents/{id}/chunks`` page — orders chunks with
one stable rule. That helper must be *reused*, never copied:

    ``chunk_index`` → legacy ``_NNNN_`` id index → raw chunk id

``source_locator`` normalization (B1.3) also lives here so the PDF / image /
DOCX/Markdown/TXT / none mapping is a single source of truth shared by the
single-chunk and chunk-list endpoints.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_LEGACY_ID_INDEX = re.compile(r"_(\d{4})_")

# File-extension families used only to *pick* a locator kind. The parser is
# not changed; this only decides how honest we can be about a source position.
_PDF_EXTS = frozenset({".pdf"})
_IMAGE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
})
_SECTION_EXTS = frozenset({".docx", ".md", ".markdown", ".txt"})

# Image-oriented chunk content types (produced by the vision transforms).
_IMAGE_CONTENT_TYPES = frozenset({"image_ocr", "image_caption"})


def chunk_sort_key(hit: dict[str, Any]) -> tuple[int, int, str]:
    """Stable ordering key shared by MCP + Web chunk lists.

    Returns ``(source_rank, index, id)``:
    - rank 0: authoritative ``chunk_index`` metadata,
    - rank 1: numeric index embedded in a legacy chunk id (``_NNNN_``),
    - rank 2: raw chunk id (ties only where neither of the above exists).
    """
    meta = hit.get("metadata") or {}
    idx = meta.get("chunk_index")
    if idx is not None:
        try:
            return (0, int(idx), str(hit.get("id") or ""))
        except (TypeError, ValueError):
            pass
    chunk_id = str((hit.get("id") or "") or str(meta.get("chunk_id") or ""))
    match = _LEGACY_ID_INDEX.search(chunk_id)
    if match:
        try:
            return (1, int(match.group(1)), chunk_id)
        except (TypeError, ValueError):
            pass
    return (2, 0, chunk_id)


def stable_order_chunks(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a deterministic ordering of chunk dicts (never mutates input)."""
    return sorted(hits, key=chunk_sort_key)


def chunk_id_of(hit: dict[str, Any]) -> str:
    """Resolve a chunk's stable id from the vector-store hit dict."""
    meta = hit.get("metadata") or {}
    return str((hit.get("id") or "") or str(meta.get("chunk_id") or ""))


def page_number_of(metadata: dict[str, Any]) -> int | None:
    """1-based page int from metadata (``page_num`` / ``page``) or None."""
    page = metadata.get("page_num")
    if page is None:
        page = metadata.get("page")
    try:
        return int(page) if page is not None else None
    except (TypeError, ValueError):
        return None


def heading_of(hit: dict[str, Any]) -> str | None:
    """Best-effort section heading for a chunk, or None.

    Prefers explicit metadata (``heading`` / ``section``), then the first
    Markdown heading line inside the chunk's text. This mirrors the summary
    building in ``web_api.mappers.to_document_detail`` so single-chunk and
    document-detail agree.
    """
    text = str(hit.get("text") or "")
    meta = hit.get("metadata") or {}
    for key in ("heading", "section"):
        raw = meta.get(key)
        if raw and str(raw).strip():
            return str(raw)
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            stripped = line.lstrip("#").strip()
            if stripped:
                return stripped
    return None


def _file_ext(source_path: str | None) -> str:
    if not source_path:
        return ""
    return Path(source_path).suffix.lower()


def build_source_locator(
    metadata: dict[str, Any],
    *,
    source_path: str | None = None,
    content_type: str | None = None,
    heading: str | None = None,
) -> dict[str, Any]:
    """Normalize a chunk's source-locator dict (task book B1.3).

    Returns ``{"kind": ..., "page": int|None}`` where ``kind`` is one of
    ``pdf_page`` / ``image`` / ``section`` / ``none``. We never guess: when
    there is no reliable position the kind is ``none`` and ``page`` is null.
    """
    meta = metadata or {}
    ctype = content_type or str(meta.get("content_type") or "")
    page = page_number_of(meta)
    ext = _file_ext(source_path or str(meta.get("source_path") or ""))

    if ctype in _IMAGE_CONTENT_TYPES or ext in _IMAGE_EXTS:
        # An image locator points at the whole image, so there is no page.
        return {"kind": "image", "page": None}
    if ext in _PDF_EXTS:
        if page is not None and page >= 1:
            return {"kind": "pdf_page", "page": page}
        return {"kind": "none", "page": None}
    if ext in _SECTION_EXTS:
        has_heading = bool(
            heading
            or str(meta.get("heading") or meta.get("section") or "").strip()
        )
        if has_heading:
            return {"kind": "section", "page": None}
    return {"kind": "none", "page": None}


__all__ = [
    "build_source_locator",
    "chunk_id_of",
    "chunk_sort_key",
    "heading_of",
    "page_number_of",
    "stable_order_chunks",
]